"""B5.6 — resolucion del proveedor de columnas SIN calificar en un FROM con varios origenes (defecto del parser: la evidencia de campos de cada entidad existia en el pool y no se cruzaba).

SQL exige que una columna sin calificar sea inequivoca: si TODOS los proveedores del FROM son entidades con campos conocidos y EXACTAMENTE UNO expone el campo (comparacion exacta, como los
identificadores entrecomillados), ese es el proveedor. Si algun proveedor es desconocido o mas de uno expone el nombre, NO se adivina (se conserva el comportamiento anterior).
"""
from __future__ import annotations

from types import SimpleNamespace

from tools.catalog.modeling_assessment import derivation as DV
from tools.catalog.modeling_assessment import facts_harmonization_contract as FH


def view(from_node, columns):
    return {"kind": "entity", "query": {"SELECT": {"from": from_node, "columns": columns}}}


def join(*sources):
    node = {"ref": [sources[0]]}
    for s in sources[1:]:
        node = {"join": "left", "args": [node, {"ref": [s]}], "on": [{"ref": ["x"]}, "=", {"ref": [s, "k"]}]}
    return node


def ents(**fields):
    return {name: SimpleNamespace(fields=[{"campo": f, "label": f"L{f}"} for f in fs]) for name, fs in fields.items()}


def cols(defn):
    return {c.output: c for c in DV.extract_projection_columns("V", defn)}


def test_unqualified_columns_over_several_entities_carry_their_candidate_providers_and_stay_unresolved_by_the_reader():
    c = cols(view(join("A", "B"), [{"ref": ["vbeln"]}, {"ref": ["B", "k"]}]))
    assert (c["vbeln"].source_provider, c["vbeln"].candidates) == (None, ("A", "B"))            # el lector por archivo no puede cruzar el catalogo: queda para el resolutor
    assert (c["k"].source_provider, c["k"].candidates) == ("B", ())                              # calificada: igual que antes


def test_single_source_and_qualified_references_behave_exactly_as_before():
    c = cols(view({"ref": ["A"]}, [{"ref": ["vbeln"]}]))
    assert (c["vbeln"].source_provider, c["vbeln"].candidates) == ("A", ())
    with_sub = {"kind": "entity", "query": {"SELECT": {"from": {"join": "left", "args": [{"ref": ["A"]}, {"SELECT": {"from": {"ref": ["C"]}, "columns": [{"ref": ["z"]}]}, "as": "S"}], "on": []},
                                                  "columns": [{"ref": ["vbeln"]}]}}}
    assert cols(with_sub)["vbeln"].candidates == ()                                              # un subselect entre los origenes: no resoluble


def test_to_json_is_unchanged_so_the_persisted_evidence_shape_does_not_change():
    c = cols(view(join("A", "B"), [{"ref": ["vbeln"]}]))["vbeln"]
    assert set(c.to_json()) == {"output", "kind", "source_provider", "source_field", "branch"}


def resolve(col, entities):
    return FH.resolve_unqualified_provider(col, entities)


def test_the_provider_is_resolved_when_exactly_one_known_entity_exposes_the_field():
    col = cols(view(join("A", "B", "C"), [{"ref": ["vbeln"]}]))["vbeln"]
    assert resolve(col, ents(A=["vbeln", "posnr"], B=["matnr"], C=["werks"])) == "A"


def test_it_never_guesses_when_the_field_is_ambiguous_or_a_provider_is_unknown_or_the_match_is_not_exact():
    col = cols(view(join("A", "B"), [{"ref": ["vbeln"]}]))["vbeln"]
    assert resolve(col, ents(A=["vbeln"], B=["vbeln"])) is None                                  # dos entidades lo exponen: ambiguo
    assert resolve(col, ents(A=["vbeln"])) is None                                               # B no esta en el pool: no se puede afirmar la unicidad
    assert resolve(col, {"A": SimpleNamespace(fields=[{"campo": "vbeln"}]), "B": SimpleNamespace(fields=[])}) is None   # B sin campos conocidos
    assert resolve(col, ents(A=["VBELN"], B=["otro"])) is None                                   # comparacion EXACTA (identificadores entrecomillados): VBELN != vbeln
    assert resolve(col, ents(A=["otro"], B=["otro2"])) is None                                   # nadie lo expone


def test_a_resolved_provider_makes_the_label_origin_determinable_instead_of_unknown():
    """El efecto buscado: sin proveedor la etiqueta propia de un campo era UNKNOWN (no se puede comparar con el proveedor); con el proveedor se compara y se determina su origen."""
    entities = ents(A=["vbeln"], B=["matnr"])
    entities["A"].fields[0]["label"] = "Entrega"
    col = cols(view(join("A", "B"), [{"ref": ["vbeln"]}]))["vbeln"]
    unresolved = FH._provider_label(entities, None, "vbeln")
    assert unresolved == (None, False) and FH._label_origin("vbeln", "Entrega", "vbeln", *unresolved) == FH.LO_UNKNOWN
    prov = resolve(col, entities)
    pl, known = FH._provider_label(entities, prov, "vbeln")
    assert known and FH._label_origin("vbeln", "Entrega", "vbeln", pl, known) == FH.LO_PROVIDER_LABEL
    assert FH._label_origin("vbeln", "Etiqueta propia", "vbeln", pl, known) == FH.LO_DIFFERENT      # una etiqueta distinta del proveedor conocido SI es vocabulario propio
