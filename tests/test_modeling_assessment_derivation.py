"""
G3 (Gate 4) — semantica estructural de las derivaciones desde el CSN compilado (``derivation.py``).

Contrato de relacion (fijo): A=DUPLICATED_TRANSFORMATION, B=CHAINED_REFINEMENT (NO es una desviacion), C=DISTINCT_TRANSFORMATION, D=UNKNOWN.
Sin parser SQL, sin regex, sin heuristica por nombre de campo, sin valor por defecto silencioso. Fixtures SINTETICAS con la misma FORMA que los
casos reales conocidos (refinamiento de signo sobre un campo ya derivado; re-agregacion).
"""
from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path

import pytest

from tools.catalog.modeling_assessment import derivation as D
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.model import LEGACY_SIGNAL_ALIASES
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence

AM = "4AM_X"


# ───────────────────────────── helpers de CSN ─────────────────────────────
def ref(*path):
    return {"ref": list(path)}


def val(v):
    return {"val": v}


def sel(frm, cols, **kw):
    return {"SELECT": {"from": frm, "columns": cols, **kw}}


def defn(query, **extra):
    return {"kind": "entity", "elements": {}, "query": query, **extra}


def case_sign_flip(field: str, flag: str = "vbtyp") -> dict:
    """CASE WHEN flag = 'T' THEN field * -1 ELSE field END (refinamiento de signo sobre el campo ya derivado)."""
    return {"as": field, "xpr": ["case", "when", ref(flag), "=", val("T"), "then", ref(field), "*", val(-1), "else", ref(field), "end"]}


def dosis_base(field: str = "DOSIS") -> dict:
    return {"as": field, "xpr": ["case", "when", ref("MEINH"), "=", val("DS"), "then",
                                 {"func": "COALESCE", "args": [{"xpr": [ref("FACTORCONVERSION"), "*", ref("lfimg")]}, val(0)]}, "else", val(0), "end"]}


def cols_of(d: dict, artifact: str = "ART"):
    return D.extract_derived_columns(artifact, d)


# ───────────────────────────── 1-3. extraccion SELECT / grafica / SET ─────────────────────────────
def test_sql_select_ast_extraction_classifies_operations_and_inputs():
    d = defn(sel({"ref": ["SRC"], "as": "s"}, [ref("s", "K"), dosis_base(), {"as": "TOT", "func": "SUM", "args": [ref("s", "AMT")]},
                                                 {"as": "FULL", "xpr": [ref("s", "A"), "||", ref("s", "B")]}]))
    got = {c.field: c for c in cols_of(d)}
    assert set(got) == {"DOSIS", "TOT", "FULL"}                                      # K (pasaje directo) no es derivada
    assert got["DOSIS"].operations == ("ARITHMETIC", "CONDITIONAL", "FUNCTION") and got["DOSIS"].functions == ("COALESCE",)
    assert got["TOT"].operations == ("AGGREGATION",) and got["FULL"].operations == ("CONCATENATION",)
    assert [(i.provider, i.field, i.state) for i in got["TOT"].inputs] == [("SRC", "AMT", "RESOLVED")]
    assert all(c.resolvable for c in got.values())


def test_graphical_view_compiled_select_ast_is_extracted_exactly_like_a_sql_view(tmp_path):
    body = defn(sel({"ref": ["SRC"]}, [dosis_base()]), **{"@DataWarehouse.sqlEditor.query": "SELECT 1"})
    sql_file, gfx_file = tmp_path / "SQL_V.json", tmp_path / "GFX_V.json"
    sql_file.write_text(json.dumps({"definitions": {"V": body}}), encoding="utf-8")
    gfx_file.write_text(json.dumps({"definitions": {"V": {k: v for k, v in body.items() if not k.startswith("@DataWarehouse.sqlEditor")}},
                                    "editorSettings": {"V": {"editor": {"default": "GRAPHICALVIEWBUILDER"}, "uiModel": "{}"}}}), encoding="utf-8")
    a = SupplementaryEvidence.from_files([sql_file], root=tmp_path).derivation_evidence("V")
    b = SupplementaryEvidence.from_files([gfx_file], root=tmp_path).derivation_evidence("V")
    assert a is not None and b is not None and a.columns and [c.exact_fp for c in a.columns] == [c.exact_fp for c in b.columns]
    assert a.has_query and b.has_query


def test_query_set_branches_are_extracted_per_branch():
    b0 = sel({"ref": ["T1"]}, [{"as": "F", "xpr": [ref("X"), "+", val(1)]}])
    b1 = sel({"ref": ["T2"]}, [{"as": "F", "xpr": [ref("Y"), "*", val(2)]}])
    d = {"kind": "entity", "query": {"SET": {"op": "union", "all": True, "args": [b0, b1]}}}
    cols = cols_of(d)
    assert [(c.field, c.branch) for c in cols] == [("F", 0), ("F", 1)] and cols[0].exact_fp != cols[1].exact_fp
    assert [i.provider for c in cols for i in c.inputs] == ["T1", "T2"]
    nested = {"kind": "entity", "query": {"SET": {"op": "union", "args": [{"SET": {"op": "union", "args": [b0, b1]}}, b1]}}}
    assert len(D.select_branches(nested)) == 3


def test_a_definition_without_query_has_no_branches_and_no_columns():
    assert D.select_branches({"kind": "entity", "elements": {}}) == [] and cols_of({"kind": "entity"}) == ()


# ───────────────────────────── 4-6. resolucion determinista y ambiguedad ─────────────────────────────
def test_source_field_resolution_is_deterministic_through_join_aliases():
    frm = {"join": "inner", "args": [{"ref": ["ORD"], "as": "o"}, {"ref": ["CUST"], "as": "c"}], "on": []}
    d = defn(sel(frm, [{"as": "X", "xpr": [ref("o", "A"), "+", ref("c", "B")]}]))
    (c,) = cols_of(d)
    assert sorted((i.provider, i.field, i.state) for i in c.inputs) == [("CUST", "B", "RESOLVED"), ("ORD", "A", "RESOLVED")] and c.resolvable


def test_unqualified_reference_with_several_sources_is_unknown_not_guessed():
    frm = {"join": "inner", "args": [{"ref": ["ORD"]}, {"ref": ["CUST"]}], "on": []}
    (c,) = cols_of(defn(sel(frm, [{"as": "X", "xpr": [ref("A"), "+", val(1)]}])))
    assert not c.resolvable and c.ambiguity == ("UNQUALIFIED_MULTI_SOURCE",) and c.inputs[0].provider is None


def test_subselect_source_and_unresolved_alias_are_explicit_ambiguity():
    inner = {"SELECT": {"from": {"ref": ["T"]}, "columns": [ref("A")]}, "as": "sub"}
    (c1,) = cols_of(defn(sel(inner, [{"as": "X", "xpr": [ref("sub", "A"), "+", val(1)]}])))
    (c2,) = cols_of(defn(sel({"ref": ["T"], "as": "t"}, [{"as": "X", "xpr": [ref("ghost", "A"), "+", val(1)]}])))
    assert c1.ambiguity == ("SUBSELECT_SOURCE",) and c2.ambiguity == ("UNRESOLVED_ALIAS",)
    (c3,) = cols_of(defn(sel({"ref": ["T"]}, [{"as": "X", "xpr": [{"SELECT": {"from": {"ref": ["U"]}, "columns": [ref("K")]}}, "+", val(1)]}])))
    assert "SUBQUERY_IN_EXPRESSION" in c3.ambiguity


def test_parameters_are_not_column_inputs():
    (c,) = cols_of(defn(sel({"ref": ["T"]}, [{"as": "X", "xpr": [ref("A"), "+", ref("$parameters", "P")]}])))
    assert {(i.field, i.state) for i in c.inputs} == {("A", "RESOLVED"), ("P", "PARAMETER")} and c.resolvable


# ───────────────────────────── 7-10. huellas ─────────────────────────────
def test_exact_fingerprint_is_stable_and_independent_of_key_order_and_run():
    a = defn(sel({"ref": ["S"]}, [dosis_base()]))
    reordered = json.loads(json.dumps(a, sort_keys=True))
    assert cols_of(a)[0].exact_fp == cols_of(reordered)[0].exact_fp == cols_of(copy.deepcopy(a))[0].exact_fp


def test_shape_fingerprint_is_stable_for_the_same_form():
    a = cols_of(defn(sel({"ref": ["S"]}, [dosis_base()])))[0]
    b = cols_of(defn(sel({"ref": ["S"]}, [dosis_base("OTRO")])))[0]
    assert a.shape_fp == b.shape_fp and a.exact_fp == b.exact_fp                         # el nombre de salida (as) no es parte de la estructura


def test_alias_and_qualifier_changes_do_not_alter_the_exact_structure():
    mk = lambda alias, src: cols_of(defn(sel({"ref": [src], "as": alias}, [{"as": "X", "xpr": [ref(alias, "A"), "*", val(2)]}])))[0]
    assert mk("a", "S1").exact_fp == mk("zzz", "S2").exact_fp


def test_literals_and_references_affect_only_the_shape_abstraction():
    base = cols_of(defn(sel({"ref": ["S"]}, [{"as": "X", "xpr": [ref("A"), "*", val(2)]}])))[0]
    other_literal = cols_of(defn(sel({"ref": ["S"]}, [{"as": "X", "xpr": [ref("A"), "*", val(3)]}])))[0]
    other_field = cols_of(defn(sel({"ref": ["S"]}, [{"as": "X", "xpr": [ref("B"), "*", val(2)]}])))[0]
    other_operator = cols_of(defn(sel({"ref": ["S"]}, [{"as": "X", "xpr": [ref("A"), "+", val(2)]}])))[0]
    assert base.exact_fp != other_literal.exact_fp and base.shape_fp == other_literal.shape_fp
    assert base.exact_fp != other_field.exact_fp and base.shape_fp == other_field.shape_fp
    assert base.exact_fp != other_operator.exact_fp and base.shape_fp != other_operator.shape_fp


def test_keywords_and_aggregate_names_are_case_insensitive_in_the_structure():
    a = cols_of(defn(sel({"ref": ["S"]}, [{"as": "X", "xpr": ["CASE", "WHEN", ref("A"), "=", val(1), "THEN", val(2), "ELSE", val(0), "END"]}])))[0]
    b = cols_of(defn(sel({"ref": ["S"]}, [{"as": "X", "xpr": ["case", "when", ref("A"), "=", val(1), "then", val(2), "else", val(0), "end"]}])))[0]
    assert a.exact_fp == b.exact_fp


# ───────────────────────────── 11-13, 14-17: relaciones ─────────────────────────────
def _pair(provider_cols, consumer_cols, *, consumer_from=None, provider_from=None):
    p = cols_of(defn(sel(provider_from or {"ref": ["SRC"]}, provider_cols)), "PRV")
    c = cols_of(defn(sel(consumer_from or {"ref": ["PRV"]}, consumer_cols)), "CON")
    return p, c


def _rel(p, c, field, chain=("PRV",)):
    return D.classify_relation([x for x in p if x.field == field], [x for x in c if x.field == field], field=field, chain_sources=chain)


def test_chained_refinement_consumer_reading_the_derived_provider_field_with_a_sign_flip():
    p, c = _pair([dosis_base("DOSIS"), {"as": "VALOR_DECLARADO", "xpr": [ref("KONP", "KBETR"), "*", ref("lfimg")]}],
                 [case_sign_flip("DOSIS"), case_sign_flip("VALOR_DECLARADO")])
    assert _rel(p, c, "DOSIS") == ("CHAINED_REFINEMENT", "CONSUMER_READS_DERIVED_PROVIDER_FIELD")
    assert _rel(p, c, "VALOR_DECLARADO") == ("CHAINED_REFINEMENT", "CONSUMER_READS_DERIVED_PROVIDER_FIELD")


def test_chained_refinement_for_a_reaggregation_of_a_derived_measure():
    p, c = _pair([{"as": "Peso", "func": "SUM", "args": [ref("NTGEW")]}], [{"as": "Peso", "func": "SUM", "args": [ref("PRV", "Peso")]}],
                 consumer_from={"join": "inner", "args": [{"ref": ["PRV"]}, {"ref": ["OTHER"]}], "on": []})
    assert _rel(p, c, "Peso")[0] == "CHAINED_REFINEMENT"


def test_a_chained_refinement_can_never_be_emitted_as_a_duplicated_transformation():
    # estructura exacta IDENTICA a la del proveedor, pero el consumidor LEE el campo ya derivado: la causalidad estructural manda
    prov = {"as": "F", "xpr": [ref("F"), "+", val(1)]}
    p, c = _pair([prov], [dict(prov)])
    rel, reason = _rel(p, c, "F")
    assert p[0].exact_fp == c[0].exact_fp and rel == "CHAINED_REFINEMENT" and reason == "CONSUMER_READS_DERIVED_PROVIDER_FIELD"
    variants = [
        ([ref("F")], None), ([ref("PRV", "F")], None),
        ([ref("x", "F")], {"ref": ["PRV"], "as": "x"}),
        ([ref("F")], {"join": "left", "args": [{"ref": ["PRV"], "as": "a"}], "on": []}),
    ]
    for args, frm in variants:
        pp, cc = _pair([{"as": "F", "xpr": [ref("A"), "*", val(2)]}], [{"as": "F", "func": "ROUND", "args": args}], consumer_from=frm)
        assert _rel(pp, cc, "F")[0] != "DUPLICATED_TRANSFORMATION", args


def test_a_chain_through_an_intermediate_passthrough_still_reads_the_derived_ancestor_field():
    prov = cols_of(defn(sel({"ref": ["SRC"]}, [dosis_base()])), "ANC")
    cons = cols_of(defn(sel({"ref": ["MID"]}, [case_sign_flip("DOSIS")])), "CON")
    assert D.classify_relation(prov, cons, field="DOSIS", chain_sources=("MID",))[0] == "CHAINED_REFINEMENT"      # MID es proveedor directo que desciende de ANC


def test_duplicated_transformation_requires_the_same_structure_repeated_without_consuming_the_provider_field():
    expr = {"as": "F", "xpr": ["case", "when", ref("K"), "=", val("X"), "then", ref("A"), "else", val(0), "end"]}
    p, c = _pair([expr], [dict(expr)], consumer_from={"ref": ["OTHER_SRC"]})
    assert _rel(p, c, "F") == ("DUPLICATED_TRANSFORMATION", "IDENTICAL_STRUCTURE")


def test_distinct_transformation_same_name_different_structure_and_inputs():
    p, c = _pair([{"as": "F", "xpr": [ref("A"), "*", ref("B")]}],
                 [{"as": "F", "xpr": ["case", "when", ref("C"), "=", val("X"), "then", ref("D"), "else", val(0), "end"]}], consumer_from={"ref": ["ELSEWHERE"]})
    assert _rel(p, c, "F") == ("DISTINCT_TRANSFORMATION", "DIFFERENT_STRUCTURE")


def test_same_shape_and_inputs_differing_only_in_literals_is_unknown_not_a_guessed_duplicate():
    p, c = _pair([{"as": "F", "xpr": [ref("A"), "*", val(2)]}], [{"as": "F", "xpr": [ref("A"), "*", val(3)]}], consumer_from={"ref": ["ELSEWHERE"]})
    assert _rel(p, c, "F") == ("UNKNOWN", "NEAR_EQUIVALENT_UNPROVEN")


def test_ambiguous_cases_are_unknown_never_defaulted():
    frm = {"join": "inner", "args": [{"ref": ["PRV"]}, {"ref": ["OTHER"]}], "on": []}
    p, c = _pair([{"as": "F", "xpr": [ref("A"), "*", val(2)]}], [{"as": "F", "xpr": [ref("F"), "*", val(-1)]}], consumer_from=frm)
    assert _rel(p, c, "F") == ("UNKNOWN", "AMBIGUOUS_REFERENCE_TO_SAME_FIELD")             # ¿F es de PRV o de OTHER? no se adivina
    assert D.classify_relation([], c, field="F", chain_sources=("PRV",)) == ("UNKNOWN", "NO_QUERY_EVIDENCE")
    sub = {"SELECT": {"from": {"ref": ["T"]}, "columns": [ref("A")]}, "as": "q"}
    p2, c2 = _pair([{"as": "F", "xpr": [ref("A"), "*", val(2)]}], [{"as": "F", "xpr": [ref("q", "B"), "+", val(1)]}], consumer_from=sub)
    assert _rel(p2, c2, "F")[0] == "UNKNOWN"
    b0 = sel({"ref": ["PRV"]}, [{"as": "F", "xpr": [ref("F"), "+", val(1)]}])
    b1 = sel({"ref": ["OTHER"]}, [{"as": "F", "xpr": [ref("A"), "+", val(1)]}])
    cs = D.extract_derived_columns("CON", {"query": {"SET": {"op": "union", "args": [b0, b1]}}})
    assert D.classify_relation(cols_of(defn(sel({"ref": ["S"]}, [{"as": "F", "xpr": [ref("A"), "*", val(2)]}])), "PRV"), cs, field="F", chain_sources=("PRV",))[1] == "BRANCHES_DISAGREE"


def test_a_repeated_field_name_alone_is_insufficient_to_decide_anything():
    p, c = _pair([{"as": "F", "xpr": [ref("A"), "+", val(1)]}], [{"as": "F", "xpr": [ref("B"), "||", val("x")]}], consumer_from={"ref": ["ELSEWHERE"]})
    assert _rel(p, c, "F")[0] == "DISTINCT_TRANSFORMATION"                                 # mismo nombre, trabajo distinto: NO es duplicacion
    assert _rel(p, c, "F")[0] != "DUPLICATED_TRANSFORMATION"


def test_the_classifier_has_no_regex_sql_text_parser_or_field_name_heuristic():
    src = (Path(__file__).resolve().parents[1] / "tools" / "catalog" / "modeling_assessment" / "derivation.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"^\s*(import|from)\s+(re|sqlparse|sqlglot)\b", code, re.M)
    assert "sqlEditor" not in code and ".split(" not in code                                      # no se lee el texto SQL ni se parte por texto
    assert not re.search(r"startswith\(['\"](VR|VF|2VR|3VF)", code)                              # sin heuristicas por prefijo/nombre


# ───────────────────────────── integracion: hecho persistido y senal ─────────────────────────────
def _w(root: Path, rel: str, body) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _pool(vr_cols, vf_cols, am_cols=None, *, extra_views=None, vf_from=None) -> Path:
    """AM <- 3VF <- 2VR <- LOCAL_T (tabla local). Mismas formas que los casos reales, nombres genericos."""
    root = Path(tempfile.mkdtemp(prefix="deriv_"))
    el = lambda *names: {n: {"type": "cds.String", "@EndUserText.label": n} for n in names}
    _w(root, "Acme/local-tables/LOCAL_T.json", {"definitions": {"LOCAL_T": {"kind": "entity", "elements": el("MEINH", "FACTORCONVERSION", "lfimg", "vbtyp", "NTGEW", "K")}}})
    _w(root, "Acme/views/2VR_X.json", {"definitions": {"2VR_X": {"kind": "entity", "elements": el("K", "DOSIS", "VALOR_DECLARADO", "Peso", "vbtyp"),
        "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}, "query": sel({"ref": ["LOCAL_T"]}, vr_cols)}}})
    _w(root, "Acme/views/3VF_X.json", {"definitions": {"3VF_X": {"kind": "entity", "elements": el("K", "DOSIS", "VALOR_DECLARADO", "Peso"),
        "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_FACT"}, "query": sel(vf_from or {"ref": ["2VR_X"]}, vf_cols)}}})
    amc = am_cols if am_cols is not None else [ref("3VF_X", "K"), ref("3VF_X", "DOSIS")]
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: {"kind": "entity", "elements": el("K", "DOSIS"),
        "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"}, "@Analytics.dataCategory": {"#": "CUBE"}, "query": sel({"ref": ["3VF_X"]}, amc)}}})
    for name, body in (extra_views or {}).items():
        _w(root, f"Acme/views/{name}.json", body)
    return root


def _assess(root: Path) -> dict:
    doc, _ = ma_shadow.assess(root, "Acme", AM, "General")
    return doc


def _repeated(doc: dict) -> list[dict]:
    return doc["canonical_evidence"]["derivation_semantics"]["repeated_derivations"]


def test_known_shape_c3_dosis_and_valor_declarado_are_chained_refinement_with_root_exposure():
    root = _pool([ref("K"), dosis_base("DOSIS"), {"as": "VALOR_DECLARADO", "xpr": [ref("KONP", "KBETR"), "*", ref("lfimg")]}, ref("vbtyp")],
                 [ref("K"), case_sign_flip("DOSIS"), case_sign_flip("VALOR_DECLARADO")], am_cols=[ref("K"), ref("DOSIS")])
    # VALOR_DECLARADO del proveedor referencia KONP (alias no declarado en `from`): ambiguedad EXPLICITA, la cadena igual se prueba por el consumidor
    doc = _assess(root)
    rel = {r["field"]: r for r in _repeated(doc)}
    assert set(rel) == {"DOSIS", "VALOR_DECLARADO"}
    assert rel["DOSIS"]["relation"] == rel["VALOR_DECLARADO"]["relation"] == "CHAINED_REFINEMENT"
    assert rel["DOSIS"]["relation_reason"] == "CONSUMER_READS_DERIVED_PROVIDER_FIELD"
    assert rel["DOSIS"]["adjacent"] is True and rel["DOSIS"]["provider"] == "2VR_X" and rel["DOSIS"]["consumer"] == "3VF_X"
    assert rel["DOSIS"]["exposed_by_root"] is True and rel["DOSIS"]["exposure_basis"] == "SAME_NAME_IN_ROOT_CONTRACT"
    assert rel["VALOR_DECLARADO"]["exposed_by_root"] is False                                     # el AM no expone ese campo
    assert doc["canonical_evidence"]["derivation_semantics"]["relation_counts"] == {"DUPLICATED_TRANSFORMATION": 0, "CHAINED_REFINEMENT": 2,
                                                                                     "DISTINCT_TRANSFORMATION": 0, "UNKNOWN": 0}
    group = next(g for g in doc["review_required"] if g["id"] == "REPEATED_DERIVATION_IN_LINEAGE")
    fr = group["occurrences"][0]["values"]["field_relations"]
    assert [(x["field"], x["relation"]) for x in fr] == [("DOSIS", "CHAINED_REFINEMENT"), ("VALOR_DECLARADO", "CHAINED_REFINEMENT")]


def test_known_shape_illolay_reaggregation_is_chained_refinement():
    root = _pool([ref("K"), {"as": "Peso", "func": "SUM", "args": [ref("NTGEW")]}], [ref("K"), {"as": "Peso", "func": "SUM", "args": [ref("2VR_X", "Peso")]}],
                 am_cols=[ref("K")], vf_from={"join": "inner", "args": [{"ref": ["2VR_X"]}, {"ref": ["LOCAL_T"]}], "on": []})
    (r,) = _repeated(_assess(root))
    assert (r["field"], r["relation"]) == ("Peso", "CHAINED_REFINEMENT") and r["exposed_by_root"] is False


def test_distinct_transformation_and_unknown_are_persisted_with_their_reason():
    distinct = _pool([ref("K"), {"as": "DOSIS", "xpr": [ref("lfimg"), "*", ref("NTGEW")]}],
                     [ref("K"), {"as": "DOSIS", "xpr": ["case", "when", ref("K"), "=", val("A"), "then", val(1), "else", val(0), "end"]}],
                     vf_from={"join": "inner", "args": [{"ref": ["2VR_X"]}, {"ref": ["LOCAL_T"]}], "on": []})
    # el consumidor no lee DOSIS y su estructura difiere; con 2 origenes `K` y `lfimg` no calificados => la rama C exige insumos resueltos
    (r,) = _repeated(_assess(distinct))
    assert r["relation"] in ("DISTINCT_TRANSFORMATION", "UNKNOWN") and r["relation_reason"] != "CONSUMER_READS_DERIVED_PROVIDER_FIELD"
    unknown = _pool([ref("K"), {"as": "DOSIS", "xpr": [ref("lfimg"), "*", val(2)]}], [ref("K"), {"as": "DOSIS", "xpr": [ref("q", "DOSIS"), "*", val(2)]}],
                    vf_from={"SELECT": {"from": {"ref": ["2VR_X"]}, "columns": [ref("K")]}, "as": "q"})
    (u,) = _repeated(_assess(unknown))
    assert u["relation"] == "UNKNOWN"


def test_sibling_identical_expression_is_not_a_lineage_rederivation_but_descriptive_reuse_evidence():
    expr = lambda src: {"as": "Fecha", "func": "TO_DATE", "args": [ref(src, "DATAB")]}
    sib = lambda name: {"definitions": {name: {"kind": "entity", "elements": {}, "query": sel({"ref": ["LOCAL_T"], "as": "LOCAL_T"}, [expr("LOCAL_T")])}}}
    root = _pool([ref("K")], [ref("K")], extra_views={"VR_SIB_A": sib("VR_SIB_A"), "VR_SIB_B": sib("VR_SIB_B")}, vf_from={
        "join": "inner", "args": [{"ref": ["2VR_X"]}, {"ref": ["VR_SIB_A"]}, {"ref": ["VR_SIB_B"]}], "on": []})
    doc = _assess(root)
    assert _repeated(doc) == []                                                                   # sin re-derivacion de propagacion
    rep = doc["canonical_evidence"]["expression_repetition"]
    assert rep["count"] == 1 and rep["groups"][0]["kind"] == "SIBLING" and rep["groups"][0]["sibling_pairs"] == 1 and rep["groups"][0]["lineage_related_pairs"] == 0
    assert sorted(m["artifact"] for m in rep["groups"][0]["members"]) == ["VR_SIB_A", "VR_SIB_B"]
    assert not [g for g in (*doc["observations"], *doc["review_required"], *doc["descriptive_signals"]) if "EXPRESSION" in g["id"]]   # sin senal ni direccionalidad


def test_derivation_semantics_fact_is_persisted_without_forbidden_vocabulary_and_is_deterministic():
    root = _pool([ref("K"), dosis_base("DOSIS")], [ref("K"), case_sign_flip("DOSIS")], am_cols=[ref("K"), ref("DOSIS")])
    a, b = _assess(root), _assess(root)
    assert ma_profile.canonical_assessment_json(a) == ma_profile.canonical_assessment_json(b)
    sem = a["canonical_evidence"]["derivation_semantics"]
    assert sem["assessability"] == "ASSESSED" and sem["extractor_version"] == "derivation-extractor/v1"
    f = next(x for x in sem["fields"] if x["artifact"] == "3VF_X" and x["field"] == "DOSIS")
    assert set(f) >= {"artifact", "field", "branch", "operations", "functions", "inputs", "exact_fp", "shape_fp", "resolvable", "ambiguity",
                      "reads_provider_same_field", "exposed_by_root"}
    assert f["reads_provider_same_field"] is True and f["exposed_by_root"] is True
    ma_profile.validate_assessment(a)


def test_a_pool_without_query_evidence_reports_not_assessed_never_an_invented_relation():
    doc = _assess(_pool([ref("K"), dosis_base("DOSIS")], [ref("K"), case_sign_flip("DOSIS")]))
    f = next(x for x in doc["fact_index"] if x["fact"] == "propagation.derivation_semantics@closure")
    assert f["assessability"] == "ASSESSED"                                                       # con query en el pool
    # sin evidencia suplementaria (hechos construidos sin lector) la relacion es UNKNOWN/NO_QUERY_EVIDENCE: ver tests de hechos de propagacion


# ───────────────────────────── perfiles historicos y G4 ─────────────────────────────
def test_profiles_persisted_with_the_old_signal_id_remain_readable_and_valid():
    doc = _assess(_pool([ref("K"), dosis_base("DOSIS")], [ref("K"), case_sign_flip("DOSIS")]))
    old = copy.deepcopy(doc)
    old.pop("canonical_evidence")
    for g in old["review_required"]:
        if g["id"] == "REPEATED_DERIVATION_IN_LINEAGE":
            g["id"] = "CROSS_LAYER_REDERIVATION"
            for o in g["occurrences"]:
                o["values"].pop("field_relations", None)
    for r in old["rule_results"]:
        if r["rule"] == "REPEATED_DERIVATION_IN_LINEAGE":
            r["rule"] = "CROSS_LAYER_REDERIVATION"
    for area in old["areas"]:
        area["finding_groups"] = ["CROSS_LAYER_REDERIVATION" if g == "REPEATED_DERIVATION_IN_LINEAGE" else g for g in area["finding_groups"]]
    ma_profile.validate_assessment(old)
    from tools.catalog.modeling_assessment import render_md
    md = render_md.render_markdown(old)
    assert "(distancia de linaje 1)" in md and "refinamiento encadenado (no es una desviación)" not in md        # el perfil viejo no trae relación: no se inventa
    assert LEGACY_SIGNAL_ALIASES == {"CROSS_LAYER_REDERIVATION": "REPEATED_DERIVATION_IN_LINEAGE"}


def test_assembly_topology_is_descriptive_without_thresholds_or_signals():
    doc = _assess(_pool([ref("K")], [ref("K")], vf_from={"join": "inner", "args": [{"ref": ["2VR_X"]}, {"ref": ["LOCAL_T"]}], "on": []}))
    topo = doc["canonical_evidence"]["assembly_topology"]
    (a,) = [x for x in topo["artifacts"] if x["artifact"] == "3VF_X"]
    assert set(a) == {"artifact", "direct_providers", "boundary_providers", "intermediate_providers", "reusable_intermediate_providers", "lineage_consumers",
                      "lineage_distance_to_root", "reaches_root_by_strict_lineage", "closure_leaves_reached", "closure_leaves_total", "share_of_closure_leaves"}
    assert (a["direct_providers"], a["boundary_providers"], a["intermediate_providers"]) == (2, 1, 1)
    from tools.catalog.modeling_assessment import assessability as A
    roles = {s.surface_id: s.role for s in A.SURFACE_REGISTRY}
    assert roles["harmonization.assembly_topology"] is A.SurfaceRole.DERIVED_VIEW and roles["harmonization.expression_repetition"] is A.SurfaceRole.DERIVED_VIEW
    assert [g["id"] for g in doc["review_required"] if g["id"] == "FLAT_CENTRAL_ASSEMBLY_REVIEW"] == []          # sin cambiar la semantica de la revision existente
