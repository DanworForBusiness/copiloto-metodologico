"""
B5.1 — enlace puro (FUERA del motor) entre hojas de un perfil y ratificaciones `governed-decision/v1` vigentes, y simulación extremo a extremo con la variante opt-in.

Sin I/O: el historial se inyecta en memoria. Vigencia + identidad + digest MATCH se resuelven acá; el motor solo valida el resultado contra el perfil.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from tests._judgment_cases import fresh
from tools.catalog.methodological_judgment import evaluate_judgment
from webapp.services import governed_decisions as gd
from webapp.services import governed_ratification_link as link

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
CLIENT = "Biogenesis"
CRITICAL = ["ART_0012", "ART_0013", "ART_0014", "ART_0015"]
VARIANT = "GOVERNED_CONTRACT_RATIFIED"


def gp() -> dict:
    p = fresh("biogenesis_sellout_gp")
    for r in p["canonical_evidence"]["source_contract_classes"]["leaves"]:
        if r["leaf_kind"] == "REMOTE_IMPORTED":
            r["remote_connection"], r["remote_entities"] = "SQL_CONN", [f'"dbo"."{r["leaf"]}"']
    return p


def leaf_rec(p: dict, leaf: str) -> dict:
    return next(r for r in p["canonical_evidence"]["source_contract_classes"]["leaves"] if r["leaf"] == leaf)


def tid_of(p: dict, leaf: str) -> str:
    return gd.cds_technical_id(gd.cds_identity(leaf_rec(p, leaf)))


def ratified(p: dict, leaf: str, *, event_id: str = "gd-1", decided_at: str = "2026-10-01T00:00:00Z", client: str = CLIENT, review_by: str | None = None) -> dict:
    subject = {"client": client, "technical_id": tid_of(p, leaf)}
    return gd.build_ratification(event_id=event_id, subject=subject, rationale="contrato gobernado por el área de datos", actor="owner@danwor.com", decided_at=decided_at,
                                 evidence_ref_override=gd.cds_evidence_ref(leaf_rec(p, leaf)), review_by=review_by)


def revoked(p: dict, leaf: str, *, revokes: str = "gd-1", event_id: str = "gd-2", decided_at: str = "2026-10-02T00:00:00Z") -> dict:
    return gd.build_revocation(event_id=event_id, subject={"client": CLIENT, "technical_id": tid_of(p, leaf)}, revokes_event_id=revokes, rationale="error de alcance",
                               actor="owner@danwor.com", decided_at=decided_at)


def store(*events: dict):
    def load(client: str, tid: str) -> list[dict]:
        return [e for e in events if e["subject"]["technical_id"] == tid and e["subject"]["client"] == client]
    return load


def reasons(rep: dict) -> dict:
    return {r["leaf"]: r["reason"] for r in rep["report"]}


def simulate(p: dict, load, **kw) -> tuple[dict, dict]:
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=load, **kw)
    return rep, evaluate_judgment(p, variants=[VARIANT], ratified_contracts=rep["ratified_contracts"])


# ───────────── vigencia + identidad + digest ─────────────
def test_valid_ratifications_link_and_the_simulation_resolves_only_lvl05():
    p = gp()
    base = evaluate_judgment(p)
    rep, sim = simulate(p, store(*[ratified(p, l, event_id=f"gd-{l}") for l in CRITICAL]))
    assert [c["leaf"] for c in rep["ratified_contracts"]] == CRITICAL and all(r["outcome"] == "LINKED" for r in rep["report"] if r["leaf"] in CRITICAL)
    assert (base["status"], base["level_rule"]) == ("NOT_DETERMINABLE", "LVL-05")
    assert (sim["status"], sim["level"], sim["level_rule"]) == ("DETERMINED", "ADECUADA", "LVL-03")
    assert {s["rule_id"] for s in sim["strengths"]} == {s["rule_id"] for s in base["strengths"]}                  # sin promoción de fortalezas


def test_no_ratification_means_nothing_is_linked_and_the_judgment_is_exactly_v2():
    p = gp()
    rep, sim = simulate(p, store())
    assert rep["ratified_contracts"] == [] and set(reasons(rep).values()) == {"NO_RATIFICATION"}
    base = evaluate_judgment(p)
    assert (sim["status"], sim["level_rule"], sim["reasons"], sim["strengths"]) == (base["status"], base["level_rule"], base["reasons"], base["strengths"])


def test_a_revoked_ratification_does_not_link_and_the_nd_remains():
    p = gp()
    events = [ratified(p, l, event_id=f"gd-{l}") for l in CRITICAL] + [revoked(p, "ART_0012", revokes="gd-ART_0012", event_id="gd-rev")]
    rep, sim = simulate(p, store(*events))
    assert reasons(rep)["ART_0012"] == "REVOKED" and "ART_0012" not in [c["leaf"] for c in rep["ratified_contracts"]]
    assert (sim["status"], sim["level_rule"]) == ("NOT_DETERMINABLE", "LVL-05")


def test_a_re_ratification_after_a_revocation_links_again():
    p = gp()
    events = [ratified(p, "ART_0012", event_id="gd-a", decided_at="2026-10-01T00:00:00Z"), revoked(p, "ART_0012", revokes="gd-a", event_id="gd-b", decided_at="2026-10-02T00:00:00Z"),
              ratified(p, "ART_0012", event_id="gd-c", decided_at="2026-10-03T00:00:00Z")]
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=store(*events))
    assert [c["event_id"] for c in rep["ratified_contracts"]] == ["gd-c"]


def test_stale_evidence_does_not_link_when_the_observed_contract_changed_after_the_ratification():
    p = gp()
    events = [ratified(p, l, event_id=f"gd-{l}") for l in CRITICAL]
    changed = copy.deepcopy(p)
    leaf_rec(changed, "ART_0013")["classification_bases"] = ["REMOTE_ENTITY_NAMESPACE"]                         # cambia la evidencia observada de ESA hoja
    rep, sim = simulate(changed, store(*events))
    assert reasons(rep)["ART_0013"] == "EVIDENCE_STALE" and "ART_0013" not in [c["leaf"] for c in rep["ratified_contracts"]]
    assert (sim["status"], sim["level_rule"]) == ("NOT_DETERMINABLE", "LVL-05")                                  # una sola hoja stale basta para que LVL-05 siga vigente


def test_contradictory_or_unverifiable_evidence_across_consumers_does_not_link():
    p = gp()
    events = [ratified(p, l, event_id=f"gd-{l}") for l in CRITICAL]
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=store(*events), evidence_digests={tid_of(p, "ART_0014"): None})
    assert reasons(rep)["ART_0014"] == "EVIDENCE_UNKNOWN" and "ART_0014" not in [c["leaf"] for c in rep["ratified_contracts"]]


def test_a_ratification_of_another_identity_or_another_client_never_links():
    p = gp()
    other_leaf = ratified(p, "ART_0013", event_id="gd-x")                                                         # otra identidad: no se busca para ART_0012
    other_client = ratified(p, "ART_0012", event_id="gd-y", client="Kensing")
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=store(other_leaf, other_client))
    assert reasons(rep)["ART_0012"] == "NO_RATIFICATION" and [c["leaf"] for c in rep["ratified_contracts"]] == ["ART_0013"]


def test_events_of_a_foreign_subject_returned_by_the_store_fail_closed():
    p = gp()
    ev = ratified(p, "ART_0013", event_id="gd-x")
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=lambda c, t: [ev])                              # el almacén devuelve un evento de OTRO contrato para cualquier consulta
    assert reasons(rep)["ART_0012"] == "FOREIGN_SUBJECT_EVENTS" and [c["leaf"] for c in rep["ratified_contracts"]] == ["ART_0013"]   # solo el contrato al que pertenece el evento


def test_review_due_still_links_because_an_overdue_review_is_not_an_expiry_but_it_is_reported():
    p = gp()
    ev = ratified(p, "ART_0012", event_id="gd-r", decided_at="2024-01-01T00:00:00Z", review_by="2024-06-01T00:00:00Z")
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=store(ev))
    assert [c["leaf"] for c in rep["ratified_contracts"]] == ["ART_0012"]
    assert next(r for r in rep["report"] if r["leaf"] == "ART_0012")["review"] == "DUE"


# ───────────── solo la clase indeterminada es candidata ─────────────
def test_physical_governed_unknown_and_non_remote_leaves_are_never_candidates_even_with_a_ratification_on_file():
    p = gp()
    ev_raw = ratified(p, "ART_0007", event_id="gd-raw")                                                           # tabla ABAP física cruda
    ev_gov = ratified(p, "ART_0010", event_id="gd-gov")                                                           # CDS ya gobernada
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=store(ev_raw, ev_gov))
    assert rep["ratified_contracts"] == [] and {"ART_0007", "ART_0010"}.isdisjoint(reasons(rep))
    sim = evaluate_judgment(p, variants=[VARIANT], ratified_contracts=[{"leaf": "ART_0007", "identity": gd.cds_identity(leaf_rec(p, "ART_0007")), "event_id": "gd-raw", "digest": ev_raw["evidence_ref"]["digest"], "evidence_ref": ev_raw["evidence_ref"]}])
    assert next(d for d in sim["dispositions_applied"] if d.get("leaf") == "ART_0007")["reject_code"] == "SOURCE_NOT_ELIGIBLE"      # defensa en profundidad: el motor también lo rechaza


def test_an_ambiguous_identity_is_not_linked():
    p = gp()
    leaf_rec(p, "ART_0012")["remote_entities"] = ['"dbo"."A"', '"dbo"."B"']
    rep = link.link_ratifications(p, CLIENT, now=NOW, load_events=store())
    assert reasons(rep)["ART_0012"] == "IDENTITY_NOT_UNEQUIVOCAL"


# ───────────── no escribe ni muta ─────────────
def test_the_link_is_read_only_it_never_mutates_the_profile_or_the_events():
    p = gp()
    events = [ratified(p, l, event_id=f"gd-{l}") for l in CRITICAL]
    p0, e0 = copy.deepcopy(p), copy.deepcopy(events)
    simulate(p, store(*events))
    assert p == p0 and events == e0


def test_the_linked_item_carries_the_exact_event_and_evidence_reference_and_the_latest_event_wins():
    p = gp()
    old = ratified(p, "ART_0012", event_id="gd-old", decided_at="2026-10-01T00:00:00Z")
    new = ratified(p, "ART_0012", event_id="gd-new", decided_at="2026-10-05T00:00:00Z")
    rep, sim = simulate(p, store(old, new))
    item = next(c for c in rep["ratified_contracts"] if c["leaf"] == "ART_0012")
    assert (item["event_id"], item["evidence_ref"], item["technical_id"], item["decided_at"]) == ("gd-new", new["evidence_ref"], new["subject"]["technical_id"], "2026-10-05T00:00:00Z")
    rec = next(d for d in sim["dispositions_applied"] if d.get("leaf") == "ART_0012")
    assert (rec["event_id"], rec["evidence_ref"], rec["technical_id"]) == ("gd-new", new["evidence_ref"], new["subject"]["technical_id"])        # trazable al evento vigente, no al viejo
