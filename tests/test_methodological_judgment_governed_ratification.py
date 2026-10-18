"""
B5.1 — variante opt-in ``GOVERNED_CONTRACT_RATIFIED`` (SIMULACION pura): una ratificación de gobierno vigente resuelve ÚNICAMENTE la indeterminación contractual decisión-crítica (LVL-05)
de una hoja ``EXTERNAL_CONTRACT_UNDETERMINED``. No convierte la hoja en gobernada para el resto de las reglas, no otorga fortalezas y sin ella ``judgment-rules/v2`` queda byte a byte igual.

El motor NO resuelve vigencia/digest (eso es del enlace, fuera del motor): acá se prueba la validación CONTRA EL PERFIL y el efecto aislado en el nivel.
"""
from __future__ import annotations

import copy
import glob
import os

import pytest

from tests._judgment_cases import FIXTURES, fresh
from tests._judgment_profiles import PB
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
from tools.catalog.methodological_judgment.rules import decision_critical_undetermined_source

VARIANT = "GOVERNED_CONTRACT_RATIFIED"
ALIASES = sorted(os.path.basename(f)[: -len(".json.gz")] for f in glob.glob(str(FIXTURES / "*.json.gz")))
CRITICAL = ["ART_0012", "ART_0013", "ART_0014", "ART_0015"]          # fixture biogenesis_sellout_gp: las 4 hojas que disparan LVL-05
HEX = "a" * 64


def with_identities(p: dict) -> dict:
    """El fixture anonimizado comparte una sola identidad entre hojas: se les da una identidad única y estable (conexión + entidad) para poder ratificar de a una."""
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf_kind"] == "REMOTE_IMPORTED":
            r["remote_connection"], r["remote_entities"] = "SQL_CONN", [f'"dbo"."{r["leaf"]}"']
    return p


def ratif(p: dict, leaf: str, *, event_id: str | None = None, **override) -> dict:
    rec = next(r for r in p["canonical_evidence"]["source_contract_classes"]["leaves"] if r["leaf"] == leaf)
    item = {"leaf": leaf, "identity": {"remote_connection": rec["remote_connection"], "remote_entity": rec["remote_entities"][0]}, "event_id": event_id or f"gd-{leaf}", "digest": HEX,
            "technical_id": f"tid:{leaf}", "evidence_ref": {"algo": "sha256", "fields": ["source_contract_classes.leaves[cds]"], "digest": HEX}}
    item.update(override)
    return item


def run(p: dict, ratifications):
    return evaluate_judgment(p, variants=[VARIANT], ratified_contracts=ratifications)


def gp() -> dict:
    return with_identities(fresh("biogenesis_sellout_gp"))


def shape(j: dict) -> tuple:
    return (j["status"], j["level"], j["level_rule"], j.get("not_determinable_reason"))


def rec_for(j: dict, leaf: str) -> dict:
    return next(d for d in j["dispositions_applied"] if d.get("leaf") == leaf)


# ───────────── sin la variante: judgment-rules/v2 intacto ─────────────
@pytest.mark.parametrize("alias", ALIASES)
def test_without_the_variant_the_output_is_byte_identical_and_unaffected_by_the_new_code(alias):
    p = fresh(alias)
    a, b = evaluate_judgment(p), evaluate_judgment(copy.deepcopy(p), variants=())
    assert a["output_signature"] == b["output_signature"] and "ruleset_options" not in a and a["ruleset_version"] == "judgment-rules/v2"
    assert all(d.get("decision") != VARIANT for d in a["dispositions_applied"])


def test_ratified_contracts_without_the_explicit_variant_is_rejected_not_silently_ignored():
    p = gp()
    with pytest.raises(ValueError, match="GOVERNED_CONTRACT_RATIFIED"):
        evaluate_judgment(p, ratified_contracts=[ratif(p, CRITICAL[0])])


@pytest.mark.parametrize("alias", ALIASES)
def test_the_variant_without_ratifications_changes_nothing_but_the_declared_option(alias):
    p = fresh(alias)
    base, sim = evaluate_judgment(p), evaluate_judgment(p, variants=[VARIANT])
    for k in ("status", "level", "level_rule", "closure", "reasons", "strengths", "review_candidates", "evidence_limitations", "dispositions_applied", "not_determinable_reason"):
        assert base.get(k) == sim.get(k), k
    assert sim["ruleset_options"]["variants"] == [VARIANT] and sim["ruleset_options"]["ratified_contracts"]["resolved_leaves"] == []


def test_inputs_are_not_mutated_and_the_simulation_is_deterministic():
    p = gp()
    before = copy.deepcopy(p)
    r = [ratif(p, l) for l in CRITICAL]
    a, b = run(p, r), run(p, list(reversed(r)))
    assert p == before and a["output_signature"] == b["output_signature"]


# ───────────── ratificación válida: efecto aislado en LVL-05 ─────────────
def test_the_baseline_is_the_nd_lvl05_with_four_decision_critical_leaves():
    p = gp()
    assert sorted(decision_critical_undetermined_source(ProfileEvidence(p), "UNDETERMINED")) == CRITICAL
    assert shape(evaluate_judgment(p)) == ("NOT_DETERMINABLE", None, "LVL-05", "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL")


def test_all_valid_ratifications_resolve_lvl05_and_the_level_comes_only_from_the_remaining_rules_without_promoting_strengths():
    p = gp()
    base, sim = evaluate_judgment(p), run(p, [ratif(p, l) for l in CRITICAL])
    assert shape(sim) == ("DETERMINED", "ADECUADA", "LVL-03", None)              # el nivel lo determinan las razones RELEVANT ya existentes
    assert [(r["rule_id"], r["classification"]) for r in sim["reasons"]] == [(r["rule_id"], r["classification"]) for r in base["reasons"]]
    assert {s["rule_id"] for s in sim["strengths"]} == {s["rule_id"] for s in base["strengths"]}          # NO suma SRC-STR-01, PUB-STR-01 ni otras fortalezas
    assert not {"SRC-STR-01", "PUB-STR-01"} & {s["rule_id"] for s in sim["strengths"]}
    assert [rec_for(sim, l)["status"] for l in CRITICAL] == ["APPLIED"] * 4
    assert all(rec_for(sim, l)["scope"] == "LVL-05_SOURCE_CONTRACT_CLASS_ONLY" and rec_for(sim, l)["decision"] == VARIANT for l in CRITICAL)
    assert sim["ruleset_options"]["ratified_contracts"] == {"scope": "LVL-05_SOURCE_CONTRACT_CLASS_ONLY", "resolved_leaves": CRITICAL, "promotes_strengths": False}
    assert sim["ruleset_version"] == "judgment-rules/v2"


def test_the_leaf_class_is_not_changed_for_any_other_rule_so_the_source_review_stays_open():
    p = gp()
    sim = run(p, [ratif(p, l) for l in CRITICAL])
    ev = ProfileEvidence(p)
    assert all(ev.classify_source(l) == "EXTERNAL_CONTRACT_UNDETERMINED" for l in CRITICAL)             # la clase explícita no se reescribe
    assert any(r["review_id"].startswith("EXTERNAL_CONTRACT_UNDETERMINED") or r["rule_id"] == "SRC-REV-01" for r in sim["review_candidates"])
    lim = next(l for l in sim["evidence_limitations"] if "external_source_contract_undetermined" in str(l.get("limitation_id", l)))
    assert lim["can_change_level"] is False                                                             # ya no es decisión-crítica una vez resuelta


def test_a_partial_ratification_leaves_the_remaining_critical_leaves_unresolved():
    p = gp()
    sim = run(p, [ratif(p, l) for l in CRITICAL[:3]])
    assert shape(sim) == ("NOT_DETERMINABLE", None, "LVL-05", "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL")
    assert sim["ruleset_options"]["ratified_contracts"]["resolved_leaves"] == CRITICAL[:3]


# ───────────── ratificaciones no válidas contra el perfil ─────────────
@pytest.mark.parametrize("override,code", [
    ({"identity": {"remote_connection": "SQL_CONN", "remote_entity": '"dbo"."OTRA"'}}, "IDENTITY_MISMATCH"),
    ({"identity": {"remote_connection": "OTRA_CONN", "remote_entity": '"dbo"."ART_0012"'}}, "IDENTITY_MISMATCH"),
    ({"identity": {"remote_connection": None, "remote_entity": '"dbo"."ART_0012"'}}, "IDENTITY_MISMATCH"),
    ({"leaf": "NO_EXISTE"}, "UNKNOWN_LEAF"),
    ({"digest": "corto"}, "INVALID_RATIFICATION"),
    ({"evidence_ref": None}, "INVALID_RATIFICATION"),                                                         # sin referencia de evidencia no hay trazabilidad al evento
    ({"evidence_ref": {"algo": "md5", "fields": ["x"], "digest": HEX}}, "INVALID_RATIFICATION"),
    ({"evidence_ref": {"algo": "sha256", "fields": ["x"], "digest": "b" * 64}}, "INVALID_RATIFICATION"),      # el digest de la referencia debe ser el del evento
    ({"event_id": " "}, "INVALID_RATIFICATION"),
])
def test_a_ratification_with_the_wrong_identity_or_shape_is_rejected_and_never_changes_the_judgment(override, code):
    p = gp()
    base = evaluate_judgment(p)
    item = ratif(p, "ART_0012")
    item.update(override)
    sim = run(p, [item])
    rec = next(d for d in sim["dispositions_applied"] if d.get("decision") == VARIANT)
    assert rec["status"] == "REJECTED" and rec["reject_code"] == code
    assert shape(sim) == shape(base) and sim["ruleset_options"]["ratified_contracts"]["resolved_leaves"] == []


def test_a_ratification_for_another_leafs_identity_cannot_resolve_a_different_leaf():
    p = gp()
    swapped = ratif(p, "ART_0012", identity=ratif(p, "ART_0013")["identity"])
    assert run(p, [swapped])["dispositions_applied"][-1]["reject_code"] == "IDENTITY_MISMATCH"


@pytest.mark.parametrize("leaf,why", [("ART_0007", "tabla física ABAP cruda (evidencia positiva de frontera cruda)"), ("ART_0010", "contrato CDS ya gobernado"), ("ART_0000", "contrato ODP/SAPI ya gobernado")])
def test_physical_or_already_governed_sources_are_not_eligible_a_human_ratification_never_reclassifies_positive_evidence(leaf, why):
    p = gp()
    base = evaluate_judgment(p)
    sim = run(p, [ratif(p, leaf)])
    assert rec_for(sim, leaf)["status"] == "REJECTED" and rec_for(sim, leaf)["reject_code"] == "SOURCE_NOT_ELIGIBLE", why
    assert shape(sim) == shape(base) and {r["rule_id"] for r in sim["reasons"]} == {r["rule_id"] for r in base["reasons"]}


def test_unknown_class_platform_class_and_non_remote_leaves_are_not_eligible():
    for cls, kind in (("UNKNOWN", "REMOTE_IMPORTED"), ("PLATFORM_PROVIDED", "REMOTE_IMPORTED"), ("EXTERNAL_CONTRACT_UNDETERMINED", "LOCAL")):
        p = gp()
        rec = next(r for r in p["canonical_evidence"]["source_contract_classes"]["leaves"] if r["leaf"] == "ART_0011")
        rec["contract_class"], rec["leaf_kind"] = cls, kind
        sim = run(p, [ratif(p, "ART_0011")])
        assert rec_for(sim, "ART_0011")["reject_code"] == "SOURCE_NOT_ELIGIBLE", (cls, kind)


def test_a_valid_ratification_of_an_undetermined_leaf_without_decision_critical_exposure_has_no_effect():
    p = gp()
    base = evaluate_judgment(p)
    sim = run(p, [ratif(p, "ART_0011"), ratif(p, "ART_0016")])                                # indeterminadas pero sin exposición verbatim al publicado
    assert [rec_for(sim, l)["status"] for l in ("ART_0011", "ART_0016")] == ["NO_EFFECT", "NO_EFFECT"]
    assert shape(sim) == shape(base)


def test_a_duplicate_ratification_for_the_same_leaf_is_applied_once():
    p = gp()
    sim = run(p, [ratif(p, "ART_0012", event_id="gd-1"), ratif(p, "ART_0012", event_id="gd-2")])
    assert sorted(d["status"] for d in sim["dispositions_applied"] if d.get("decision") == VARIANT) == ["APPLIED", "REJECTED"]


# ───────────── causas ND adicionales y otras reglas ─────────────
def _und_model() -> PB:
    b = PB().view("V1", ["EXT"], derived=1).remote("EXT", external=True).remote("CDS1").product("V1").measure("M")
    return b.trace("M", "EXT", character="IDENTITY_ONLY", through=False, label="EQUALS_LEAF_LABEL")


def test_resolving_lvl05_does_not_hide_an_independent_nd_cause_it_surfaces_as_lvl06():
    p = with_identities(_und_model().build())
    assert shape(evaluate_judgment(p))[2] == "LVL-05"
    sim = run(p, [ratif(p, "EXT")])
    assert rec_for(sim, "EXT")["status"] == "APPLIED"
    assert shape(sim) == ("NOT_DETERMINABLE", None, "LVL-06", "INSUFFICIENT_AFFIRMATIVE_EVIDENCE")       # sin evidencia afirmativa suficiente: sigue «No determinable», por otra causa
    assert not {"SRC-STR-01", "PUB-STR-01"} & {s["rule_id"] for s in sim["strengths"]}


@pytest.mark.parametrize("alias", ALIASES)
def test_a_ratification_never_changes_a_profile_that_was_not_blocked_by_lvl05_including_the_parcial_ones(alias):
    p = with_identities(fresh(alias))
    base = evaluate_judgment(p)
    if base["level_rule"] == "LVL-05":
        return
    leaves = [r["leaf"] for r in p["canonical_evidence"]["source_contract_classes"]["leaves"] if r["contract_class"] == "EXTERNAL_CONTRACT_UNDETERMINED" and r["leaf_kind"] == "REMOTE_IMPORTED"]
    sim = run(p, [ratif(p, l) for l in leaves])
    assert shape(sim) == shape(base) and {s["rule_id"] for s in sim["strengths"]} == {s["rule_id"] for s in base["strengths"]}


# ───────────── trazabilidad al evento concreto ─────────────
def test_every_applied_record_cites_the_exact_event_and_its_evidence_reference_not_only_the_cds_subject():
    p = gp()
    items = [ratif(p, l, event_id=f"gd-evt-{l}") for l in CRITICAL]
    sim = run(p, items)
    for it in items:
        rec = rec_for(sim, it["leaf"])
        assert rec["status"] == "APPLIED"
        assert (rec["event_id"], rec["digest"], rec["evidence_ref"], rec["technical_id"], rec["identity"]) == (it["event_id"], it["digest"], it["evidence_ref"], it["technical_id"], it["identity"])
        assert rec["evidence_ref"]["algo"] == "sha256" and rec["evidence_ref"]["fields"] and rec["evidence_ref"]["digest"] == rec["digest"]


def test_the_traceability_changes_the_output_signature_so_a_different_event_is_a_different_simulation():
    p = gp()
    a = run(p, [ratif(p, l, event_id="gd-A") for l in CRITICAL])
    b = run(p, [ratif(p, l, event_id="gd-B") for l in CRITICAL])
    assert shape(a) == shape(b) and a["output_signature"] != b["output_signature"]


# ───────────── consumidores existentes: comportamiento v2 salvo pedido explicito ─────────────
def test_no_production_module_requests_the_variant_or_passes_ratifications():
    """El unico consumidor productivo es ``assessment_service._derive_judgment`` (``evaluate_judgment(profile)``). Ningun modulo fuera del motor, del enlace y de los tests nombra la variante."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    allowed = {"tools/catalog/methodological_judgment/contract.py", "tools/catalog/methodological_judgment/engine.py", "tools/catalog/methodological_judgment/rules.py",
               "webapp/services/governed_ratification_link.py"}
    offenders = []
    for path in list((root / "tools").rglob("*.py")) + list((root / "webapp").rglob("*.py")) + list((root / "core").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ("GOVERNED_CONTRACT_RATIFIED" in text or "ratified_contracts" in text) and rel not in allowed:
            offenders.append(rel)
    assert offenders == []


def test_the_production_derivation_is_the_plain_v2_call_for_every_fixture():
    from webapp.services import assessment_service as svc
    for alias in ALIASES:
        p = fresh(alias)
        j = svc._derive_judgment(p)
        assert j["output_signature"] == evaluate_judgment(p)["output_signature"] and "ruleset_options" not in j and j["ruleset_version"] == "judgment-rules/v2"
        assert all(d.get("decision") != VARIANT for d in j["dispositions_applied"])
