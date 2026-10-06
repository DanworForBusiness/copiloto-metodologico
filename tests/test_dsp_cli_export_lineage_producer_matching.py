"""Gate A — producer discovery acquires a Replication Flow whenever one of its declared targets canonically
matches a lineage local-table leaf (``EXACT`` o la sombra de captura de cambios ``<leaf>_Delta``), con la MISMA
semántica que el resolver de procedencia (``readers.target_flows``/``canonical_target_match``).

NO cubre el alcance Space ni el walk; NO toca Judgment/Assessment/schema. Solo la paridad del matcher.
"""
from __future__ import annotations

import pytest

from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog.DSP_CLI_Export_Lineage import _canonical_target_match
from tools.catalog.modeling_assessment.readers import (
    FlowEvidence,
    REPLICATION_FLOW,
    SupplementaryEvidence,
    canonical_target_match,
)


class FakeCli:
    def __init__(self, inventories=None, objects=None, diagnostics=None):
        self.inventories = inventories or {}
        self.objects = objects or {}
        self.diagnostics = diagnostics or dsp.ExtractionDiagnostics(False)

    def list_objects(self, space, object_type):
        return [{"technicalName": x} for x in self.inventories.get(object_type, [])]

    def read_object(self, space, object_type, technical_name):
        return self.objects.get((object_type, technical_name), {})


def _rf_json(name: str, targets, *, sources=("SRC",), connections=("CONN",)) -> dict:
    return {
        "replicationflows": {
            name: {
                "targets": {t: {} for t in targets},
                "connections": {c: {} for c in connections},
                "contents": {"replicationTasks": [{"sourceObject": {"name": s}} for s in sources]},
            }
        }
    }


def _exporter(tmp_path, rf_objects: dict[str, list[str]], leaves):
    """Exporter con ``replication-flows`` habilitado, hojas ``leaves`` ya adquiridas y los RF dados en inventario+objetos."""
    inv = {t: [] for t in dsp.ROOT_OBJECT_TYPES}
    inv["replication-flows"] = sorted(rf_objects)
    objects = {("replication-flows", rf): _rf_json(rf, tgts) for rf, tgts in rf_objects.items()}
    cli = FakeCli(inv, objects)
    exp = dsp.LineageExporter(cli, "S", output_dir=tmp_path, root_object="ROOT")
    exp.effective_features = {"replication-flows"}
    for leaf in leaves:
        exp.add_node("local-tables", leaf)
    return exp


def _produces(exp) -> set[tuple[str, str]]:
    return {(e.source_name, e.target_name) for e in exp.edges if e.relation == "PRODUCES"}


def _rf_nodes(exp) -> set[str]:
    return {name for (otype, name) in exp.nodes if otype == "replication-flows"}


# --------------------------------------------------------------------------- A: exact match
def test_a_exact_target_discovers_producer(tmp_path):
    exp = _exporter(tmp_path, {"RF_X": ["X"]}, leaves=["X"])
    exp.discover_producers()
    assert ("RF_X", "X") in _produces(exp)
    assert "RF_X" in _rf_nodes(exp)


# --------------------------------------------------------------------------- B: delta shadow
def test_b_delta_shadow_target_discovers_producer_and_binds_base_leaf(tmp_path):
    exp = _exporter(tmp_path, {"RF_X": ["X_Delta"]}, leaves=["X"])
    exp.discover_producers()
    # el productor se descubre y la arista PRODUCES apunta a la hoja base X (no a X_Delta)
    assert ("RF_X", "X") in _produces(exp)
    assert ("RF_X", "X_Delta") not in _produces(exp)
    assert "RF_X" in _rf_nodes(exp)


# --------------------------------------------------------------------------- C: independent entity safety
def test_c_independent_delta_entity_is_not_collapsed(tmp_path):
    # X y X_Delta son ambas entidades reales adquiridas: X_Delta es entidad propia, NO la sombra de X.
    exp = _exporter(tmp_path, {"RF_D": ["X_Delta"]}, leaves=["X", "X_Delta"])
    exp.discover_producers()
    prod = _produces(exp)
    assert ("RF_D", "X_Delta") in prod            # produce su propia entidad por match EXACTO
    assert ("RF_D", "X") not in prod              # NO se colapsa a X


# --------------------------------------------------------------------------- D: unrelated suffix
def test_d_unrelated_suffix_does_not_match(tmp_path):
    exp = _exporter(tmp_path, {"RF_U": ["X_DeltaExtra"], "RF_V": ["Y_Delta"]}, leaves=["X"])
    exp.discover_producers()
    assert _produces(exp) == set()                # ni sufijo ajeno ni _Delta de otra hoja
    assert _rf_nodes(exp) == set()


# --------------------------------------------------------------------------- E: multiple targets, one match suffices
def test_e_one_canonical_target_match_is_sufficient(tmp_path):
    exp = _exporter(tmp_path, {"RF_M": ["UNRELATED_TABLE", "X_Delta"]}, leaves=["X"])
    exp.discover_producers()
    assert ("RF_M", "X") in _produces(exp)
    assert "RF_M" in _rf_nodes(exp)


def test_e_no_target_matches_is_skipped(tmp_path):
    exp = _exporter(tmp_path, {"RF_N": ["UNRELATED_A", "UNRELATED_B"]}, leaves=["X"])
    exp.discover_producers()
    assert _produces(exp) == set()
    assert "RF_N" not in _rf_nodes(exp)


# --------------------------------------------------------------------------- canonical matcher unit semantics
@pytest.mark.parametrize(
    "target,leaf,known,expected",
    [
        ("X", "X", None, "EXACT"),
        ("X_Delta", "X", None, "DELTA_SHADOW_SUFFIX"),
        ("X_Delta", "X", {"X"}, "DELTA_SHADOW_SUFFIX"),      # X_Delta no es entidad -> colapsa
        ("X_Delta", "X", {"X", "X_Delta"}, None),            # X_Delta es entidad propia -> NO colapsa
        ("X_DeltaExtra", "X", None, None),                   # sufijo ajeno
        ("Y_Delta", "X", None, None),                        # _Delta de otra hoja
        ("X", "Y", None, None),
    ],
)
def test_canonical_target_match_semantics(target, leaf, known, expected):
    # el matcher de adquisición (extractor) y el del resolver (paquete) coinciden en la regla a nivel de nombre
    assert _canonical_target_match(target, leaf, known) == expected
    assert canonical_target_match(target, leaf, known) == expected


# --------------------------------------------------------------------------- G: resolver and acquisition agree
def _ev(flows):
    return SupplementaryEvidence(remote_tables={}, flows=flows, entity_files={}, issues=[], files_read=1)


@pytest.mark.parametrize(
    "rf_targets,leaf,known_entities",
    [
        (("X",), "X", None),                         # exacto
        (("X_Delta",), "X", None),                   # sombra, sin guarda
        (("X_Delta",), "X", frozenset({"X"})),       # sombra, X_Delta no es entidad
        (("X_Delta",), "X", frozenset({"X", "X_Delta"})),  # X_Delta entidad propia -> sin producción
        (("Y_Delta",), "X", None),                   # sin match
    ],
)
def test_g_resolver_and_acquisition_matcher_agree(rf_targets, leaf, known_entities):
    fe = FlowEvidence(REPLICATION_FLOW, "RF", "rf.json", source_entities=("S",),
                      target_entities=tuple(rf_targets), source_systems=(), load_types=("REPLICATE",))
    ev = _ev([fe])
    resolver_flows, _amb = ev.target_flows(leaf, known_entities)
    resolver_matched = bool(resolver_flows)
    resolver_match_kind = resolver_flows[0][1] if resolver_flows else None

    # misma decisión a nivel de nombre con el matcher de ADQUISICIÓN (espejo en el extractor, legacy) y con el
    # matcher PÚBLICO del paquete. El contrato se rompe si cualquiera diverge del resolver.
    acq_kinds = {_canonical_target_match(t, leaf, known_entities) for t in rf_targets} - {None}
    pkg_kinds = {canonical_target_match(t, leaf, known_entities) for t in rf_targets} - {None}
    assert acq_kinds == pkg_kinds                                   # espejo idéntico a la regla del paquete
    assert bool(acq_kinds) == resolver_matched
    if resolver_matched:
        assert resolver_match_kind in acq_kinds
