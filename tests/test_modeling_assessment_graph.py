"""M1 — grafo de dependencias TIPADO: aristas LINEAGE vs ASSOCIATION, dos recorridos, seguridad.

Sin evaluacion: solo hechos de conectividad. Fixtures SINTETICAS con entidades reales del legacy
(`Entity`, por el bridge) para que la forma de los datos coincida con lo que carga `load_folder`.
"""
from __future__ import annotations

import random

import pytest

from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment.graph import (
    BoundaryReference, DependencyGraph, Edge, EdgeKind, EdgeOrigin, UnknownNodeError, UnresolvedReason,
    UnresolvedReference, modeling_population,
)

L, A = EdgeKind.LINEAGE, EdgeKind.ASSOCIATION


def ent(name, *, up=(), assoc=(), declared=(), raw=None, cube=False, dataflow=False, **kw):
    e = bridge.Entity(
        name=name, file=f"{name}.json", data_category="CUBE" if cube else "",
        modeling_pattern="ANALYTICAL_CUBE" if cube else kw.pop("pattern", ""),
        upstreams=list(up), assoc_upstreams=list(assoc), is_dataflow=dataflow,
        associations=[{"campo": f"_{t}", "target": t, "on": {}} for t in declared], **kw)
    if raw is not None:
        e._raw_refs = list(raw)          # el loader lo asigna dinamicamente
    return e


def pool(*ents):
    return {e.name: e for e in ents}


def _product():
    """AM --lineage--> VF --> VR --> SRC ;  AM --assoc--> DIM --lineage--> DSRC."""
    return pool(
        ent("AM", up=["VF"], assoc=["DIM"], declared=["DIM"], cube=True),
        ent("VF", up=["VR"]), ent("VR", up=["SRC"]), ent("SRC"),
        ent("DIM", up=["DSRC"]), ent("DSRC"))


# ── aristas tipadas ────────────────────────────────────────────────────────
def test_edges_are_typed_lineage_vs_association():
    g = DependencyGraph.from_entities(_product())
    by_kind = {k: {(e.consumer, e.provider) for e in g.edges if e.kind is k} for k in EdgeKind}
    assert by_kind[L] == {("AM", "VF"), ("VF", "VR"), ("VR", "SRC"), ("DIM", "DSRC")}
    assert by_kind[A] == {("AM", "DIM")}
    assert {e.origin for e in g.edges if e.kind is L} == {EdgeOrigin.UPSTREAM_FIELD}
    assert {e.origin for e in g.edges if e.kind is A} == {EdgeOrigin.DECLARED_ASSOCIATION}
    assert dict(g.edge_counts()) == {"LINEAGE": 4, "ASSOCIATION": 1}


def test_providers_and_consumers_can_be_filtered_by_kind():
    g = DependencyGraph.from_entities(_product())
    assert g.providers("AM") == ("DIM", "VF")
    assert g.providers("AM", [L]) == ("VF",)
    assert g.providers("AM", [A]) == ("DIM",)
    assert g.consumers("DIM") == ("AM",) and g.consumers("DIM", [L]) == ()


# ── dos recorridos distintos ───────────────────────────────────────────────
def test_semantic_closure_includes_association_targets_and_their_ancestors():
    g = DependencyGraph.from_entities(_product())
    assert g.semantic_closure("AM") == ("AM", "DIM", "DSRC", "SRC", "VF", "VR")


def test_lineage_closure_follows_only_lineage_edges():
    g = DependencyGraph.from_entities(_product())
    assert g.lineage_closure("AM") == ("AM", "SRC", "VF", "VR")


def test_association_edges_never_count_as_lineage_reachability():
    g = DependencyGraph.from_entities(_product())
    assert g.feeds("SRC", "AM") is True              # espina de composicion
    assert g.feeds("DIM", "AM") is False             # solo asociacion
    assert g.feeds("DSRC", "AM") is False            # esta en la clausura semantica, pero NO alimenta por linaje
    assert "DSRC" in g.semantic_closure("AM")        # "dentro de la clausura" != "alimenta al AM"
    assert g.feeds("AM", "AM") is False
    assert g.feeds("DSRC", "DIM") is True            # dentro de la dimension si hay linaje


def test_association_only_dependency():
    g = DependencyGraph.from_entities(pool(ent("AM", assoc=["D"], declared=["D"], cube=True), ent("D")))
    assert g.semantic_closure("AM") == ("AM", "D")
    assert g.lineage_closure("AM") == ("AM",)
    assert g.feeds("D", "AM") is False


def test_artifact_reached_through_both_lineage_and_association():
    g = DependencyGraph.from_entities(pool(ent("AM", up=["X"], assoc=["X"], declared=["X"], cube=True), ent("X")))
    assert [(e.provider, e.kind) for e in g.edges_of("AM")] == [("X", A), ("X", L)]   # orden determinista: consumer, provider, kind
    assert g.feeds("X", "AM") is True                # el linaje lo alimenta aunque tambien este asociado
    assert g.providers("AM") == ("X",)               # sin duplicados por tipo de arista


def test_repeated_shared_dimension_has_no_duplicates():
    ents = pool(ent("AM", up=["VF"], assoc=["DIM"], declared=["DIM"], cube=True),
                ent("VF", up=["VR"], assoc=["DIM"], declared=["DIM"]), ent("VR"), ent("DIM"))
    g = DependencyGraph.from_entities(ents)
    closure = g.semantic_closure("AM")
    assert closure == tuple(sorted(set(closure))) == ("AM", "DIM", "VF", "VR")
    assert g.consumers("DIM") == ("AM", "VF")


# ── origen de las asociaciones (fidelidad del loader legacy) ───────────────
@pytest.mark.parametrize("declared,raw,expected", [
    # asociacion declarada, sin FROM/JOIN                       -> SOLO ASSOCIATION
    (["D"], [], {(A, EdgeOrigin.DECLARED_ASSOCIATION)}),
    # FROM/JOIN que el loader degrado a assoc_upstreams         -> LINEAGE RECUPERADO (no asociacion)
    ([], ["D"], {(L, EdgeOrigin.QUERY_SOURCE_RECOVERED)}),
    # ambas cosas a la vez: se emiten las DOS aristas
    (["D"], ["D"], {(L, EdgeOrigin.QUERY_SOURCE_RECOVERED), (A, EdgeOrigin.DECLARED_ASSOCIATION)}),
    # sin `_raw_refs` ni declaracion: no se infiere linaje sin prueba
    ([], None, {(A, EdgeOrigin.ASSOCIATION_FIELD_UNDETERMINED)}),
])
def test_edge_type_follows_semantic_origin_not_the_loader_container(declared, raw, expected):
    g = DependencyGraph.from_entities(pool(ent("AM", assoc=["D"], declared=declared, raw=raw, cube=True), ent("D")))
    assert {(e.kind, e.origin) for e in g.edges_of("AM")} == expected


def test_from_join_degraded_by_the_loader_is_recovered_as_lineage():
    """Una dimension analitica cuya fuente remota el loader dejo SOLO en assoc_upstreams (la fuente es su FROM)
    debe verse como linaje: la fuente alimenta a la dimension."""
    ents = pool(ent("VD_DIM", assoc=["SRC"], raw=["SRC"], pattern="ANALYTICAL_DIMENSION"),
                ent("SRC", remote_entity="ABAP_CDS.X$P"))
    g = DependencyGraph.from_entities(ents)
    assert g.feeds("SRC", "VD_DIM") is True
    assert [(e.provider, e.kind, e.origin) for e in g.recovered_lineage_edges] == [
        ("SRC", L, EdgeOrigin.QUERY_SOURCE_RECOVERED)]
    assert g.lineage_closure("VD_DIM") == ("SRC", "VD_DIM")


def test_recovery_does_not_turn_a_true_association_into_lineage():
    """El AM solo se ASOCIA a la dimension; la fuente de la dimension alimenta a la dimension, no al AM."""
    ents = pool(ent("AM", up=["VF"], assoc=["VD_DIM"], declared=["VD_DIM"], raw=["VF"], cube=True),
                ent("VF"),
                ent("VD_DIM", assoc=["SRC"], raw=["SRC"], pattern="ANALYTICAL_DIMENSION"),
                ent("SRC", remote_entity="ABAP_CDS.X$P"))
    g = DependencyGraph.from_entities(ents)
    assert g.feeds("VF", "AM") is True
    assert g.feeds("VD_DIM", "AM") is False and g.feeds("SRC", "AM") is False      # asociacion != alimentacion
    assert g.feeds("SRC", "VD_DIM") is True
    assert g.lineage_closure("AM") == ("AM", "VF")
    assert g.semantic_closure("AM") == ("AM", "SRC", "VD_DIM", "VF")


def test_semantic_closure_is_unchanged_by_lineage_recovery():
    """Reclasificar el tipo de arista no cambia el alcance semantico: sigue igual al legacy."""
    ents = pool(ent("AM", up=["VF"], assoc=["D1", "D2"], declared=["D1"], raw=["VF", "D2"], cube=True),
                ent("VF"), ent("D1"), ent("D2"))
    g = DependencyGraph.from_entities(ents)
    assert set(g.semantic_closure("AM")) == set(bridge.cube_subgraph(ents, ents["AM"]))
    assert g.lineage_closure("AM") == ("AM", "D2", "VF")           # D2 era FROM/JOIN degradado; D1 es asociacion


def test_recovery_without_raw_refs_falls_back_to_undetermined_association_never_to_guessed_lineage():
    g = DependencyGraph.from_entities(pool(ent("V", assoc=["S"]), ent("S")))
    assert g.recovered_lineage_edges == () and g.feeds("S", "V") is False
    assert [(e.kind, e.origin) for e in g.edges] == [(A, EdgeOrigin.ASSOCIATION_FIELD_UNDETERMINED)]


def test_edge_rejects_an_origin_that_contradicts_its_kind():
    with pytest.raises(ValueError):
        Edge("A", "B", L, EdgeOrigin.DECLARED_ASSOCIATION)
    with pytest.raises(ValueError):
        Edge("A", "B", A, EdgeOrigin.UPSTREAM_FIELD)


# ── seguridad: ciclos ──────────────────────────────────────────────────────
def test_cycle_safe_closures_terminate_and_cycles_are_reported():
    ents = pool(ent("A", up=["B"], cube=True), ent("B", up=["C"]), ent("C", up=["A"]), ent("OUT"))
    g = DependencyGraph.from_entities(ents)
    assert g.semantic_closure("A") == ("A", "B", "C")
    assert g.lineage_closure("A") == ("A", "B", "C")
    assert g.cycles() == (("A", "B", "C"),)
    assert g.feeds("B", "A") is True and g.feeds("A", "B") is True


def test_cycles_can_be_asked_per_edge_kind():
    ents = pool(ent("A", assoc=["B"], declared=["B"]), ent("B", assoc=["A"], declared=["A"]))
    g = DependencyGraph.from_entities(ents)
    assert g.cycles() == (("A", "B"),)
    assert g.cycles([L]) == ()                        # el ciclo es solo semantico (asociaciones)
    assert g.cycles([A]) == (("A", "B"),)


def test_self_reference_is_recorded_as_unresolved_evidence_not_as_an_edge():
    ents = pool(ent("A", up=["A"], assoc=["A"], declared=["A"], raw=["A"], cube=True))
    g = DependencyGraph.from_entities(ents)
    assert g.edges == ()
    assert g.cycles() == ()
    reasons = {(u.kind, u.reason, u.evidence_source) for u in g.unresolved}
    assert reasons == {
        (L, UnresolvedReason.SELF_REFERENCE, "upstreams"), (A, UnresolvedReason.SELF_REFERENCE, "assoc_upstreams"),
        (A, UnresolvedReason.SELF_REFERENCE, "associations"),
        (L, UnresolvedReason.UNVERIFIED_QUERY_REFERENCE, "_raw_refs")}      # el texto del query no prueba autorreferencia
    assert g.semantic_closure("A") == ("A",)


def test_large_chain_and_large_cycle_do_not_hit_recursion_limits():
    n = 4000
    chain = pool(*[ent(f"N{i:05d}", up=[f"N{i + 1:05d}"] if i < n - 1 else []) for i in range(n)])
    g = DependencyGraph.from_entities(chain)
    assert len(g.semantic_closure("N00000")) == n
    ring = pool(*[ent(f"R{i:05d}", up=[f"R{(i + 1) % n:05d}"]) for i in range(n)])
    gr = DependencyGraph.from_entities(ring)
    assert len(gr.cycles()) == 1 and len(gr.cycles()[0]) == n
    assert len(gr.lineage_closure("R00000")) == n


# ── seguridad: referencias faltantes ───────────────────────────────────────
def test_missing_references_are_explicit_evidence_and_never_fabricated():
    ents = pool(ent("AM", up=["GHOST_UP"], assoc=["GHOST_ASSOC"], declared=["GHOST_DECL"],
                    raw=["GHOST_RAW", "VF"], cube=True), ent("VF"))
    g = DependencyGraph.from_entities(ents)                       # no debe lanzar
    assert g.nodes == ("AM", "VF")                                # no se fabrican nodos
    assert not any(g.has_node(x) for x in ("GHOST_UP", "GHOST_ASSOC", "GHOST_DECL", "GHOST_RAW"))
    missing = {(u.target, u.kind, u.evidence_source) for u in g.unresolved if u.reason is UnresolvedReason.MISSING_TARGET}
    assert missing == {("GHOST_UP", L, "upstreams"), ("GHOST_ASSOC", A, "assoc_upstreams"),
                       ("GHOST_DECL", A, "associations")}                    # referencias DECLARADAS: fiables
    unverified = {(u.target, u.kind, u.evidence_source) for u in g.unresolved
                  if u.reason is UnresolvedReason.UNVERIFIED_QUERY_REFERENCE}
    assert unverified == {("GHOST_RAW", L, "_raw_refs")}                     # texto del query: puede ser un alias interno
    # las referencias rotas no entran al scope; `VF` SI (es un FROM/JOIN que resuelve -> linaje recuperado)
    assert g.semantic_closure("AM") == ("AM", "VF")
    assert [(e.provider, e.origin) for e in g.edges_of("AM")] == [("VF", EdgeOrigin.QUERY_SOURCE_RECOVERED)]


def test_unresolved_evidence_is_deduplicated_sorted_and_scoped_to_consumers():
    ents = pool(ent("B", up=["Z", "Z"], raw=["Z"]), ent("A", up=["Y"]))
    g = DependencyGraph.from_entities(ents)
    keys = [(u.consumer, u.target, u.kind.value, u.reason.value, u.evidence_source) for u in g.unresolved]
    assert keys == sorted(keys) and len(keys) == len(set(keys))          # orden determinista y sin duplicados
    assert [u.consumer for u in g.unresolved_of(["A"])] == ["A"]


def test_traversals_from_unknown_nodes_raise_explicitly():
    g = DependencyGraph.from_entities(_product())
    for call in (lambda: g.semantic_closure("NOPE"), lambda: g.lineage_closure("NOPE"),
                 lambda: g.providers("NOPE"), lambda: g.feeds("NOPE", "AM"), lambda: g.feeds("SRC", "NOPE")):
        with pytest.raises(UnknownNodeError):
            call()


def test_edge_to_a_nonexistent_node_is_rejected_by_the_constructor():
    with pytest.raises(ValueError):
        DependencyGraph(["A"], [Edge("A", "B", L, EdgeOrigin.UPSTREAM_FIELD)])


# ── determinismo ───────────────────────────────────────────────────────────
def _snapshot(g):
    return (g.nodes, g.edges, g.unresolved, g.processing_context, g.cycles(),
            {n: (g.semantic_closure(n), g.lineage_closure(n)) for n in g.nodes})


def test_construction_is_deterministic_regardless_of_input_order():
    base = list(_product().values())
    ref = _snapshot(DependencyGraph.from_entities({e.name: e for e in base}))
    rng = random.Random(7)
    for _ in range(20):
        shuffled = base[:]
        rng.shuffle(shuffled)
        for e in shuffled:
            rng.shuffle(e.upstreams)
            rng.shuffle(e.assoc_upstreams)
        assert _snapshot(DependencyGraph.from_entities({e.name: e for e in shuffled})) == ref


def test_semantic_closure_equals_the_legacy_cube_subgraph_on_random_graphs():
    """Misma semantica que el recorrido legacy (`_cube_subgraph`) sobre grafos con ciclos, duplicados y
    referencias rotas (el legacy ignora lo que no existe en el pool)."""
    rng = random.Random(1234)
    for _ in range(150):
        n = rng.randint(1, 14)
        names = [f"N{i}" for i in range(n)]
        ents = {}
        for nm in names:
            pick = lambda: [rng.choice(names + ["MISSING"]) for _ in range(rng.randint(0, 3))]
            ents[nm] = ent(nm, up=pick(), assoc=pick(), declared=[])
        g = DependencyGraph.from_entities(ents)
        for nm in names:
            expect = set(bridge.cube_subgraph(ents, ents[nm]))
            assert set(g.semantic_closure(nm)) == expect, nm


# ── plano Processing: el grafo solo contiene lo que recibe ─────────────────
def test_dataflow_targets_are_processing_context_not_modeling_nodes():
    ents = pool(ent("AM", up=["T", "V"], cube=True), ent("T", dataflow=True, up=["DFSRC"]), ent("V"), ent("DFSRC"))
    g = DependencyGraph.from_entities(ents)
    assert g.nodes == ("AM", "DFSRC", "V")                       # T queda fuera del grafo de Modeling
    assert not g.has_node("T")
    assert g.processing_context.dataflow_targets == ("T",)       # se EXPONE como contexto complementario
    assert g.processing_context.boundary_references == (BoundaryReference("AM", "T", L, "upstreams"),)
    assert all("T" not in (e.consumer, e.provider) for e in g.edges)
    assert g.unresolved == ()                                    # NO es MISSING_TARGET
    assert g.semantic_closure("AM") == ("AM", "V")               # ni T ni el linaje que el loader le inyecta
    assert g.lineage_closure("AM") == ("AM", "V")
    assert not any(n.upper().startswith(("RF_", "DF_", "TF_", "TC_")) for n in g.nodes)


def test_references_to_dataflow_targets_from_every_source_are_boundary_not_unresolved():
    ents = pool(ent("A", up=["T"], assoc=["T"], declared=["T"], raw=["T"]), ent("T", dataflow=True))
    g = DependencyGraph.from_entities(ents)
    assert {(b.kind, b.evidence_source) for b in g.processing_context.boundary_references} == {
        (L, "upstreams"), (A, "assoc_upstreams"), (A, "associations"), (L, "_raw_refs")}
    assert g.unresolved == () and g.edges == ()


def test_modeling_graph_is_reconstructable_without_the_processing_targets():
    """El grafo de Modeling con targets de DF == el grafo del mismo pool SIN esos targets (y sin las referencias a ellos)."""
    with_df = pool(ent("AM", up=["V", "T"], assoc=["D", "T"], declared=["D"], raw=["V", "T"], cube=True),
                   ent("V", up=["S"]), ent("S"), ent("D"),
                   ent("T", dataflow=True, up=["ONLY_VIA_T"], assoc=["D"]), ent("ONLY_VIA_T"))
    without = pool(ent("AM", up=["V"], assoc=["D"], declared=["D"], raw=["V"], cube=True),
                   ent("V", up=["S"]), ent("S"), ent("D"), ent("ONLY_VIA_T"))
    a, b = DependencyGraph.from_entities(with_df), DependencyGraph.from_entities(without)
    assert (a.nodes, a.edges, a.unresolved) == (b.nodes, b.edges, b.unresolved)
    assert a.semantic_closure("AM") == b.semantic_closure("AM") and a.lineage_closure("AM") == b.lineage_closure("AM")
    assert b.processing_context.dataflow_targets == () and a.processing_context.dataflow_targets == ("T",)


def test_modeling_population_excludes_dataflow_targets():
    ents = pool(ent("A"), ent("T", dataflow=True))
    assert set(modeling_population(ents)) == {"A"}


def test_nodes_with_raw_refs_counts_availability_of_the_extra_evidence():
    g = DependencyGraph.from_entities(pool(ent("A", raw=[]), ent("B"), ent("C", raw=["A"])))
    assert g.nodes_with_raw_refs == 2


def test_query_text_identifiers_are_unverified_not_asserted_missing():
    """En datos reales los `_raw_refs` sin resolver son alias internos de vistas graficas o tablas `_Delta`
    sombra: se registran pero NUNCA se afirma que falte un artefacto."""
    ents = pool(ent("V", raw=["Calculated Columns 1", "Union 1", "$projection", "X_Delta"]))
    g = DependencyGraph.from_entities(ents)
    assert {u.reason for u in g.unresolved} == {UnresolvedReason.UNVERIFIED_QUERY_REFERENCE}
    assert {u.target for u in g.unresolved} == {"Calculated Columns 1", "Union 1", "$projection", "X_Delta"}
    assert g.nodes == ("V",) and g.edges == ()


# ── M2: consultas aditivas (distancias y dependientes transitivos) ─────────
def test_distances_are_minimum_hops_per_traversal_mode():
    g = DependencyGraph.from_entities(_product())
    assert dict(g.distances("AM", [L])) == {"AM": 0, "VF": 1, "VR": 2, "SRC": 3}
    sem = dict(g.distances("AM"))
    assert sem == {"AM": 0, "DIM": 1, "VF": 1, "VR": 2, "DSRC": 2, "SRC": 3}
    assert "DIM" not in g.distances("AM", [L])                    # la asociacion no es camino de linaje


def test_distances_take_the_shortest_path_and_survive_cycles():
    ents = pool(ent("A", up=["B", "C"]), ent("B", up=["C"]), ent("C", up=["A", "D"]), ent("D"))
    d = g = DependencyGraph.from_entities(ents).distances("A")
    assert dict(d) == {"A": 0, "B": 1, "C": 1, "D": 2}


def test_transitive_consumers_by_kind_exclude_the_node_and_are_sorted():
    g = DependencyGraph.from_entities(_product())
    assert g.transitive_consumers("SRC") == ("AM", "VF", "VR")
    assert g.transitive_consumers("SRC", [L]) == ("AM", "VF", "VR")
    assert g.transitive_consumers("DSRC") == ("AM", "DIM")
    assert g.transitive_consumers("DSRC", [L]) == ("DIM",)        # AM solo llega por asociacion
    assert g.transitive_consumers("AM") == ()


def test_new_queries_are_cycle_safe_and_reject_unknown_nodes():
    ents = pool(ent("A", up=["B"]), ent("B", up=["A"]))
    g = DependencyGraph.from_entities(ents)
    assert g.transitive_consumers("A") == ("B",) and dict(g.distances("A")) == {"A": 0, "B": 1}
    for call in (lambda: g.distances("NOPE"), lambda: g.transitive_consumers("NOPE")):
        with pytest.raises(UnknownNodeError):
            call()
