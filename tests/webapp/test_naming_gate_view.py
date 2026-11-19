"""Presentación de «Convención de nombres y nivel» (A1/A2/A3): conclusión en capas, tooltips validados, jerarquía (desglose de Gobernables) y clickabilidad
honesta (número que abre el detalle SOLO cuando la evidencia conserva la lista completa). No toca motor ni scoring: solo `assessment_presenter.naming_gate_view`."""
from __future__ import annotations

from webapp.services import assessment_presenter as ap


def _gate(**over):
    g = {"state": "BLOCK", "basis": "ACCREDITED_NON_RECOVERABLE_RATIO_ABOVE_THRESHOLD", "threshold": 0.25, "level_effect": "BUENA_TO_ADECUADA_ONLY",
         "governed": 22, "conforming": 1, "recoverable_by_declared_role": 7, "recoverable_by_type": 0, "non_recoverable": 14,
         "excluded_source_derived": 15, "excluded_system": 0, "unknown": 3, "unknown_conforming": 0, "unknown_recoverable": 0, "unknown_non_recoverable": 3,
         "ratio_nr": 0.636364, "ratio_nr_max": 0.68,
         "non_recoverable_artifacts": [f"VT_{i}" for i in range(14)], "recoverable_artifacts": [f"VD_{i}" for i in range(7)],
         "unknown_non_recoverable_artifacts": ["1LT_I_STORAGELOCATION", "ZCDS_DSP_FACT_BKPF", "ZCDS_DSP_FACT_MATDOC"]}
    g.update(over)
    return g


def _view(level_rule="LVL-09", **over):
    return ap.naming_gate_view({"level_rule": level_rule, "naming_gate": _gate(**over)})


def _cat(v, key):
    return next(c for c in v["counts"] if c["key"] == key)


def test_block_osbc_conclusion_is_layered_and_dynamic():
    v = _view()
    assert v["state"] == "BLOCK" and v["limited_level"] is True
    assert v["headline"] == "La convención de nombres impide calificar este modelo como BUENA."
    assert "22 artefactos gobernables" in v["detail"] and "14 (64%)" in v["detail"] and "25%" in v["detail"]
    assert "68%" in _cat(v, "unknown")["tip"]                                               # la cota 68% se trasladó al tooltip de «Identidad no establecida» (discreto), no a una línea fija
    assert v["ratio"] == "64%" and v["ratio_max"] == "68%" and v["threshold"] == "25%"      # sin espacio antes de % (64%, 25%, 68%)


def test_hierarchy_children_sum_to_governed_and_are_marked():
    v = _view()
    gov, conf, rec, nonrec = (_cat(v, k)["value"] for k in ("governed", "conforming", "recoverable", "non_recoverable"))
    assert gov == conf + rec + nonrec == 22
    assert _cat(v, "governed")["group"] == "head"
    assert all(_cat(v, k)["group"] == "child" for k in ("conforming", "recoverable", "non_recoverable"))
    assert all(_cat(v, k)["group"] == "head" for k in ("excluded", "unknown"))


def test_clickable_only_where_the_engine_keeps_a_complete_list():
    v = _view()
    clickable = {c["key"] for c in v["counts"] if c["artifacts"]}
    assert clickable == {"recoverable", "non_recoverable", "unknown"}                        # Gobernables/Conformes/Excluidos son agregados sin lista: no se fabrica detalle
    assert len(_cat(v, "non_recoverable")["artifacts"]) == 14 and len(_cat(v, "recoverable")["artifacts"]) == 7
    assert _cat(v, "unknown")["artifacts"] == sorted(["1LT_I_STORAGELOCATION", "ZCDS_DSP_FACT_BKPF", "ZCDS_DSP_FACT_MATDOC"])


def test_unknown_not_clickable_when_the_list_is_only_a_subset_of_the_count():
    # 3 UNKNOWN pero solo 2 son no-conformes con lista: no se puede abrir «exactamente su categoría» => sin detalle (honesto)
    v = _view(unknown=3, unknown_non_recoverable=2, unknown_non_recoverable_artifacts=["a", "b"])
    assert _cat(v, "unknown")["value"] == 3 and _cat(v, "unknown")["artifacts"] is None


def test_every_category_has_a_validated_tooltip():
    v = _view()
    for c in v["counts"]:
        assert c["tip"] and len(c["tip"]) > 20
    assert "responsabilidad" in _cat(v, "governed")["tip"].lower()
    assert "externo" in _cat(v, "excluded")["tip"].lower() or "sistema" in _cat(v, "excluded")["tip"].lower()
    assert "no se afirma" in _cat(v, "unknown")["tip"].lower()                               # identidad no establecida: no es incumplimiento demostrado


def test_pass_state_does_not_assert_a_violation():
    v = _view(level_rule="LVL-04", state="PASS", non_recoverable=0, non_recoverable_artifacts=[], ratio_nr=0.0, ratio_nr_max=0.1)
    assert v["state"] == "PASS" and v["limited_level"] is False
    assert "no impide" in v["headline"]
    assert {c["key"] for c in v["counts"] if c["artifacts"]} <= {"recoverable", "unknown"}   # no recuperables = 0 => no clickable


def test_not_accredited_is_not_presented_as_a_demonstrated_breach():
    v = _view(level_rule="LVL-09", state="NOT_ACCREDITED", governed=0, conforming=0, recoverable_by_declared_role=0, non_recoverable=0,
              non_recoverable_artifacts=[], recoverable_artifacts=[], ratio_nr=None, ratio_nr_max=0.5)
    assert v["state"] == "NOT_ACCREDITED"
    assert "no se pudo determinar" in v["headline"].lower() and "no se afirma un incumplimiento" in v["headline"].lower()
