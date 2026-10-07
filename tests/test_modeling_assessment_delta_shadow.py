"""Gate A.3 — una tabla local delta-capable X (proyección sobre su entidad física de captura de cambios D, declarada por
``X.@DataWarehouse.delta.deltaFromEntities``) NO debe desplazar la frontera/identidad semántica a D. D es la
implementación técnica interna de X: se excluye del universo semántico por el HECHO del CSN (nunca por el sufijo
"_Delta"), preservando la frontera lógica X con procedencia del RF (target físico D, ``DELTA_SHADOW_SUFFIX``).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment.readers import technical_delta_shadow_entities


# ───────────────────────── predicado ``technical_delta_shadow_entities`` (contraejemplos §6) ─────────────────────────
class _FakeEntity:
    def __init__(self, raw_refs=(), upstreams=(), associations=()):
        self._raw_refs = list(raw_refs)
        self.upstreams = list(upstreams)
        self.assoc_upstreams = []
        self.associations = list(associations)


def _csn(tmp_path, name, defs):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({"definitions": defs}), encoding="utf-8")
    return str(p)


def _wrapper(dfe, from_ref):
    return {"kind": "entity", "@DataWarehouse.delta": {"type": {"#": "ACTIVE"}, "deltaFromEntities": list(dfe)},
            "query": {"SELECT": {"from": {"ref": list(from_ref)}}}}


def _plain(from_ref=()):
    return {"kind": "entity", "query": {"SELECT": {"from": {"ref": list(from_ref)}}}}


def test_a_native_delta_wrapper_is_excluded(tmp_path):
    ents = {"X": _FakeEntity(raw_refs=["D"]), "D": _FakeEntity()}
    j = _csn(tmp_path, "X", {"X": _wrapper(["D"], ["D"]), "D": {"kind": "entity"}})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset({"D"})


def test_b_ordinary_semantic_source_is_not_excluded(tmp_path):
    ents = {"X": _FakeEntity(raw_refs=["Y"]), "Y": _FakeEntity()}
    j = _csn(tmp_path, "X", {"X": _plain(["Y"]), "Y": {"kind": "entity"}})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


def test_c_independent_entity_named_delta_without_proof_is_not_excluded(tmp_path):
    # existe una entidad X_Delta pero NINGÚN wrapper la declara en deltaFromEntities -> no se toca
    ents = {"X_Delta": _FakeEntity(), "V": _FakeEntity(raw_refs=["X_Delta"])}
    j = _csn(tmp_path, "p", {"X_Delta": {"kind": "entity"}, "V": _plain(["X_Delta"])})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


def test_d_delta_consumed_as_ordinary_source_elsewhere_is_preserved(tmp_path):
    # X es wrapper de D, pero V consume D como fuente ordinaria (sin deltaFromEntities) -> D se PRESERVA
    ents = {"X": _FakeEntity(raw_refs=["D"]), "V": _FakeEntity(raw_refs=["D"]), "D": _FakeEntity()}
    j = _csn(tmp_path, "p", {"X": _wrapper(["D"], ["D"]), "V": _plain(["D"]), "D": {"kind": "entity"}})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


def test_e_both_wrapper_and_an_independent_entity_keep_only_true_shadow(tmp_path):
    # X wrapper de D (excluible); Z es una entidad independiente -> solo D se excluye
    ents = {"X": _FakeEntity(raw_refs=["D"]), "D": _FakeEntity(), "Z": _FakeEntity()}
    j = _csn(tmp_path, "p", {"X": _wrapper(["D"], ["D"]), "D": {"kind": "entity"}, "Z": {"kind": "entity"}})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset({"D"})


def test_f_deltaFromEntities_without_structural_from_agreement_is_not_excluded(tmp_path):
    # X declara deltaFromEntities=[D] pero NO lee D en su FROM (desacuerdo estructural) -> conservador, no excluye
    ents = {"X": _FakeEntity(raw_refs=["OTHER"]), "D": _FakeEntity()}
    j = _csn(tmp_path, "X", {"X": _wrapper(["D"], ["OTHER"]), "D": {"kind": "entity"}})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


def test_g_missing_target_entity_is_not_excluded(tmp_path):
    # D no es una entidad del pool -> nada que excluir (no se fabrica)
    ents = {"X": _FakeEntity(raw_refs=["D"])}
    j = _csn(tmp_path, "X", {"X": _wrapper(["D"], ["D"])})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


def test_h_entity_without_delta_annotation_is_untouched(tmp_path):
    # HOUSEBANK exacto: sin @DataWarehouse.delta -> jamás se considera sombra
    ents = {"1TR_SAP_MD_HOUSEBANK": _FakeEntity()}
    j = _csn(tmp_path, "hb", {"1TR_SAP_MD_HOUSEBANK": {"kind": "entity"}})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


def test_suffix_alone_is_never_the_criterion(tmp_path):
    # una entidad llamada ..._Delta SIN la anotación delta de un wrapper no se excluye
    ents = {"A_Delta": _FakeEntity(), "B": _FakeEntity(raw_refs=["A_Delta"])}
    j = _csn(tmp_path, "p", {"A_Delta": {"kind": "entity"}, "B": _plain(["A_Delta"])})
    assert technical_delta_shadow_entities(ents, [j]) == frozenset()


# ───────────────────────── end-to-end por ``shadow.assess`` (§7): identidad de frontera ─────────────────────────
def _build_pool(dst, *, extra_consumer_of_D=False):
    K = os.path.join(dst, "Kensing")
    for t in ("analytic-models", "views", "local-tables", "replication-flows"):
        os.makedirs(os.path.join(K, t), exist_ok=True)
    els = {"CostCenter": {"@EndUserText.label": "Cost center", "key": True}, "Name": {"@EndUserText.label": "Name"}}
    cols = [{"ref": ["CostCenter"]}, {"ref": ["Name"]}]

    def q(ref):
        return {"SELECT": {"from": {"ref": [ref]}, "columns": cols}}

    json.dump({"definitions": {"4AM_ROOT": {"kind": "entity", "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"},
                                            "elements": dict(els), "query": q("2VD_V")}}},
              open(os.path.join(K, "analytic-models", "4AM_ROOT.json"), "w", encoding="utf-8"))
    json.dump({"definitions": {"2VD_V": {"kind": "entity", "@ObjectModel.modelingPattern": {"#": "DIMENSION"},
                                         "elements": dict(els), "query": q("1TR_X")}}},
              open(os.path.join(K, "views", "2VD_V.json"), "w", encoding="utf-8"))
    xdef = {"kind": "entity", "@DataWarehouse.delta": {"type": {"#": "ACTIVE"}, "deltaFromEntities": ["1TR_X_Delta"]},
            "elements": dict(els), "query": q("1TR_X_Delta")}
    json.dump({"definitions": {"1TR_X": xdef, "1TR_X_Delta": {"kind": "entity", "elements": dict(els)}}},
              open(os.path.join(K, "local-tables", "1TR_X.json"), "w", encoding="utf-8"))
    if extra_consumer_of_D:
        json.dump({"definitions": {"2VD_W": {"kind": "entity", "elements": dict(els), "query": q("1TR_X_Delta")}}},
                  open(os.path.join(K, "views", "2VD_W.json"), "w", encoding="utf-8"))
    json.dump({"replicationflows": {"RF_X": {"targets": {"1TR_X_Delta": {}}, "connections": {"S4_HANA": {}},
               "contents": {"replicationTasks": [{"loadType": "REPLICATE", "sourceObject": {"name": "ZCDS_X"}}]}}}},
              open(os.path.join(K, "replication-flows", "RF_X.json"), "w", encoding="utf-8"))


def _assess_identity(dst, *, filter_enabled):
    import tools.catalog.modeling_assessment.shadow as sh
    import importlib
    importlib.reload(sh)
    from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
    if not filter_enabled:                                           # simula el comportamiento PRE-fix
        sh.technical_delta_shadow_entities = lambda e, j: frozenset()
    os.environ["DANWOR_CATALOG_CSN_CACHE"] = dst
    doc, meta = sh.assess(Path(dst), "Kensing", "4AM_ROOT", "General", generic_root=True, root_type="analytic-model")
    ev = ProfileEvidence(doc)
    lpx = ev.local_leaves.get("1TR_X", {})
    return {"excluded": meta.get("technical_delta_shadows_excluded"),
            "scc_keys": sorted(ev.leaf_class_records),
            "x_target_match": (lpx.get("producer_flows") or [{}])[0].get("target_match") if lpx else None}


@pytest.fixture
def pool(tmp_path):
    dst = str(tmp_path / ".csn_cache")
    _build_pool(dst)
    yield dst


def test_e2e_before_fix_reproduces_physical_delta_frontier(pool):
    r = _assess_identity(pool, filter_enabled=False)
    assert r["scc_keys"] == ["1TR_X_Delta"]                          # la frontera se desplaza a la entidad física
    assert "1TR_X" not in r["scc_keys"]


def test_e2e_after_fix_keeps_logical_frontier_with_delta_shadow_provenance(pool):
    r = _assess_identity(pool, filter_enabled=True)
    assert r["excluded"] == ["1TR_X_Delta"]
    assert r["scc_keys"] == ["1TR_X"]                                # la frontera semántica es la tabla lógica X
    assert r["x_target_match"] == "DELTA_SHADOW_SUFFIX"              # procedencia: RF con target físico D


def test_e2e_delta_consumed_directly_is_preserved(tmp_path):
    dst = str(tmp_path / ".csn_cache")
    _build_pool(dst, extra_consumer_of_D=True)
    r = _assess_identity(dst, filter_enabled=True)
    assert r["excluded"] is None                                     # D consumida como fuente ordinaria -> NO se excluye
    assert "1TR_X_Delta" in r["scc_keys"]
