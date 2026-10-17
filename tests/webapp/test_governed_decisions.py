"""Tests focalizados de la biblioteca PURA `governed_decisions` (v4.B / B1). Sin I/O, deterministas.

Cubre: esquema/kind (D3), alcance exacto sin comodines (D4), digest contractual con inclusiones/exclusiones y estabilidad
v3→v4 (D7), validaciones (fail-closed), revocación append-only (D5), estado derivado en tres dimensiones + principal
(precisión 2) y separación de planos (una migration-approval se rechaza). NO toca Judgment/evidencia (D8)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from webapp.services import governed_decisions as gd


# ─────────────────────────── fixtures de evidencia ───────────────────────────

def _ce_v3() -> dict:
    """canonical_evidence v3 mínimo con las dos subsecciones contractuales + ruido no contractual."""
    return {
        "version": "canonical-evidence/v3",
        "published_contract": {
            # conjuntos por clave estable, en orden NO canónico a propósito (B debe ordenarlos):
            "associations": {
                "associations": [
                    {"field": "F2", "target": "T2", "category": "other_platform", "foreign_key_fields": ["a"]},
                    {"field": "F1", "target": "T1", "category": "business_dimension", "foreign_key_fields": ["b"]},
                ],
                "aggregates": {"total": 2, "by_category": {"business_dimension": 1, "other_platform": 1}},
            },
            "fields": {
                "fields": [
                    {"technical_name": "ZB", "label": "B", "datatype": {"family": "num"}},
                    {"technical_name": "ZA", "label": "A", "datatype": {"family": "char"}},
                ],
                "counts": {"fields": 2, "measures": 0},
            },
        },
        "source_contract_classes": {
            # orden NO canónico a propósito (B debe ordenarlas por `leaf`)
            "leaves": [
                {"leaf": "ZB", "contract_class": "ABAP_PHYSICAL_TABLE"},
                {"leaf": "ZA", "contract_class": "GOVERNED_CDS"},
            ],
            "by_class": [{"contract_class": "GOVERNED_CDS", "count": 1}],   # agregado → EXCLUIDO del digest
            "definition": "texto narrativo…",                              # narrativa → EXCLUIDA del digest
        },
        # ruido no contractual (debe ser IRRELEVANTE para el digest):
        "naming_evidence": {"anything": 1},
        "view_complexity": {"count": 5},
    }


def _ce_v4_from_v3(ce_v3: dict) -> dict:
    """La migración v4.C = v3 + `leaf_materialization`, SIN tocar las subsecciones contractuales."""
    ce = {k: v for k, v in ce_v3.items()}
    ce["version"] = "canonical-evidence/v4"
    ce["leaf_materialization"] = {"leaves": [{"leaf": "ZA", "materialization_kind": "REPLICA"}]}
    return ce


SUBJECT = {"client": "Kensing", "technical_id": "ZCDS_BILLING_DOC_ITEM_BASIC"}
T0 = "2026-10-17T12:00:00+00:00"


def _ratify(ce: dict, **over) -> dict:
    kw = dict(event_id="evt-1", subject=SUBJECT, canonical_evidence=ce, rationale="contrato gobernado revisado",
              actor="joconnor@danwor.com", decided_at=T0)
    kw.update(over)
    return gd.build_ratification(**kw)


# ─────────────────────────── digest contractual (D7) ───────────────────────────

def test_digest_excludes_narrative_and_aggregates_and_noise():
    ce = _ce_v3()
    base = gd.contract_evidence_digest(ce)
    # cambiar narrativa/agregado/ruido NO cambia el digest
    ce2 = _ce_v3()
    ce2["source_contract_classes"]["definition"] = "otro texto"
    ce2["source_contract_classes"]["by_class"] = [{"contract_class": "X", "count": 99}]
    ce2["naming_evidence"] = {"anything": 999}
    ce2["view_complexity"] = {"count": 123}
    assert gd.contract_evidence_digest(ce2) == base


def test_digest_is_stable_across_v3_to_v4_migration():
    ce3 = _ce_v3()
    ce4 = _ce_v4_from_v3(ce3)
    assert gd.contract_evidence_digest(ce4) == gd.contract_evidence_digest(ce3)   # propiedad central de D7


def test_digest_is_order_independent_for_leaves():
    ce = _ce_v3()
    shuffled = _ce_v3()
    shuffled["source_contract_classes"]["leaves"] = list(reversed(shuffled["source_contract_classes"]["leaves"]))
    assert gd.contract_evidence_digest(shuffled) == gd.contract_evidence_digest(ce)


def test_digest_changes_when_contract_changes():
    ce = _ce_v3()
    changed_fields = _ce_v3()
    changed_fields["published_contract"]["fields"]["counts"]["fields"] = 11
    assert gd.contract_evidence_digest(changed_fields) != gd.contract_evidence_digest(ce)
    changed_leaf = _ce_v3()
    changed_leaf["source_contract_classes"]["leaves"][0]["contract_class"] = "RAW"
    assert gd.contract_evidence_digest(changed_leaf) != gd.contract_evidence_digest(ce)


def test_published_contract_none_is_explicit_not_omitted():
    ce = _ce_v3()
    ce["published_contract"] = None
    sub = gd.extract_contract_evidence(ce)
    assert "published_contract" in sub and sub["published_contract"] is None
    # un digest distinto del caso con contrato
    assert gd.contract_evidence_digest(ce) != gd.contract_evidence_digest(_ce_v3())


def test_evidence_ref_shape():
    ref = gd.evidence_ref(_ce_v3())
    assert ref["algo"] == "sha256" and ref["fields"] == list(gd.CONTRACT_DIGEST_FIELDS)
    assert len(ref["digest"]) == 64


# ─────────────────────────── validación / esquema / alcance ───────────────────────────

def test_unknown_kind_rejected():
    ev = _ratify(_ce_v3())
    ev["kind"] = "DESIGN_EXCEPTION"
    with pytest.raises(gd.UnsupportedKind):
        gd.validate_event(ev)


def test_wildcards_in_subject_rejected():
    for bad in ({"client": "*", "technical_id": "X"}, {"client": "Kensing", "technical_id": "ZCDS_*"},
                {"client": "Kensing", "technical_id": "  "}):
        with pytest.raises(gd.ValidationError):
            gd.build_ratification(event_id="e", subject=bad, canonical_evidence=_ce_v3(),
                                  rationale="x", actor="a@d.com", decided_at=T0)


def test_subject_rejects_extra_keys():
    with pytest.raises(gd.ValidationError):
        gd.build_ratification(event_id="e", subject={"client": "K", "technical_id": "Z", "root": "R"},
                              canonical_evidence=_ce_v3(), rationale="x", actor="a@d.com", decided_at=T0)


def test_rationale_required():
    with pytest.raises(gd.ValidationError):
        gd.build_ratification(event_id="e", subject=SUBJECT, canonical_evidence=_ce_v3(),
                              rationale="   ", actor="a@d.com", decided_at=T0)


def test_review_by_default_is_12_months_and_within_24():
    ev = _ratify(_ce_v3())
    assert ev["review_by"].startswith("2027-10-17")                # +12m
    # tope: +24m válido, +25m inválido
    gd.build_ratification(event_id="e", subject=SUBJECT, canonical_evidence=_ce_v3(), rationale="x",
                          actor="a@d.com", decided_at=T0, review_by="2028-10-17T12:00:00+00:00")
    with pytest.raises(gd.ValidationError):
        gd.build_ratification(event_id="e", subject=SUBJECT, canonical_evidence=_ce_v3(), rationale="x",
                              actor="a@d.com", decided_at=T0, review_by="2028-11-17T12:00:00+00:00")


def test_revocation_requires_reason_and_target():
    with pytest.raises(gd.ValidationError):
        gd.build_revocation(event_id="r", subject=SUBJECT, revokes_event_id="evt-1", rationale="", actor="a@d.com", decided_at=T0)
    rev = gd.build_revocation(event_id="r", subject=SUBJECT, revokes_event_id="evt-1", rationale="error", actor="a@d.com", decided_at=T0)
    assert rev["event"] == gd.EVENT_REVOKED and "evidence_ref" not in rev and "review_by" not in rev


# ─────────────────────────── separación de planos (invariante 1) ───────────────────────────

def test_migration_approval_is_rejected_as_decision():
    approval = {"schema": "migration-approval/v1", "plan_digest": "abc", "approver": "x"}
    with pytest.raises(gd.SchemaError):
        gd.validate_event(approval)
    with pytest.raises(gd.SchemaError):
        gd.assert_is_governed_decision(approval)


def test_governed_decision_with_plan_digest_is_rejected():
    ev = _ratify(_ce_v3())
    ev["plan_digest"] = "deadbeef"
    with pytest.raises(gd.SchemaError):
        gd.validate_event(ev)


# ─────────────────────────── estado derivado (3 dimensiones + principal) ───────────────────────────

def _now(s: str) -> datetime:
    return gd.parse_utc(s)


def test_status_none_when_no_events():
    st = gd.derive_status([], gd.contract_evidence_digest(_ce_v3()), _now("2026-10-18T00:00:00Z"))
    assert st["decision"] == "NONE" and st["principal"] == "NONE" and st["evidence"] == "NA" and st["review"] == "NA"


def test_status_ratified_match_current():
    ce = _ce_v3()
    ev = _ratify(ce)
    st = gd.derive_status([ev], gd.contract_evidence_digest(ce), _now("2026-10-18T00:00:00Z"))
    assert (st["decision"], st["evidence"], st["review"], st["principal"]) == ("RATIFIED", "MATCH", "CURRENT", "RATIFIED")
    assert st["ratified_event_id"] == "evt-1"


def test_status_evidence_stale_when_contract_changed():
    ce = _ce_v3()
    ev = _ratify(ce)
    changed = _ce_v3()
    changed["published_contract"]["fields"]["counts"]["fields"] = 11
    st = gd.derive_status([ev], gd.contract_evidence_digest(changed), _now("2026-10-18T00:00:00Z"))
    assert st["decision"] == "RATIFIED" and st["evidence"] == "STALE" and st["principal"] == "EVIDENCE_STALE"


def test_status_review_due_is_not_expiry_and_coexists_with_stale():
    ce = _ce_v3()
    ev = _ratify(ce)                                   # review_by = 2027-10-17
    changed = _ce_v3(); changed["source_contract_classes"]["leaves"][0]["contract_class"] = "RAW"
    st = gd.derive_status([ev], gd.contract_evidence_digest(changed), _now("2027-12-01T00:00:00Z"))
    # coexisten: evidencia STALE y revisión DUE; la decisión sigue RATIFIED (no expira)
    assert st["decision"] == "RATIFIED"
    assert st["evidence"] == "STALE" and st["review"] == "DUE"
    assert st["principal"] == "EVIDENCE_STALE"        # evidencia domina en la etiqueta, sin perder review=DUE


def test_status_review_due_principal_when_evidence_matches():
    ce = _ce_v3()
    ev = _ratify(ce)
    st = gd.derive_status([ev], gd.contract_evidence_digest(ce), _now("2027-12-01T00:00:00Z"))
    assert st["review"] == "DUE" and st["evidence"] == "MATCH" and st["principal"] == "REVIEW_DUE"
    assert st["decision"] == "RATIFIED"               # DUE no es caducidad


def test_status_evidence_unknown_is_its_own_principal():
    ev = _ratify(_ce_v3())
    st = gd.derive_status([ev], None, _now("2026-10-18T00:00:00Z"))
    # evidencia no verificable → principal EVIDENCE_UNKNOWN explícito; sigue RATIFIED (≠ revocación/vencimiento)
    assert st["evidence"] == "UNKNOWN" and st["decision"] == "RATIFIED" and st["principal"] == "EVIDENCE_UNKNOWN"


def test_status_evidence_unknown_dominates_review_due():
    ev = _ratify(_ce_v3())                                  # review_by = 2027-10-17
    st = gd.derive_status([ev], None, _now("2027-12-01T00:00:00Z"))
    assert st["evidence"] == "UNKNOWN" and st["review"] == "DUE" and st["decision"] == "RATIFIED"
    assert st["principal"] == "EVIDENCE_UNKNOWN"            # UNKNOWN domina sobre REVIEW_DUE, sin perder review=DUE


def test_digest_invariant_to_association_and_field_order():
    ce = _ce_v3()
    shuffled = _ce_v3()
    shuffled["published_contract"]["associations"]["associations"] = list(reversed(ce["published_contract"]["associations"]["associations"]))
    shuffled["published_contract"]["fields"]["fields"] = list(reversed(ce["published_contract"]["fields"]["fields"]))
    assert gd.contract_evidence_digest(shuffled) == gd.contract_evidence_digest(ce)


def test_digest_sensitive_to_field_datatype_change():
    ce = _ce_v3()
    changed = _ce_v3()
    changed["published_contract"]["fields"]["fields"][0]["datatype"] = {"family": "DECIMAL", "precision": 23, "scale": 2}
    assert gd.contract_evidence_digest(changed) != gd.contract_evidence_digest(ce)


def test_digest_sensitive_to_association_added_or_removed():
    ce = _ce_v3()
    added = _ce_v3()
    added["published_contract"]["associations"]["associations"].append({"field": "F3", "target": "T3", "category": "other_platform", "foreign_key_fields": []})
    assert gd.contract_evidence_digest(added) != gd.contract_evidence_digest(ce)
    removed = _ce_v3()
    removed["published_contract"]["associations"]["associations"] = removed["published_contract"]["associations"]["associations"][:1]
    assert gd.contract_evidence_digest(removed) != gd.contract_evidence_digest(ce)


def test_traceability_is_optional_outside_digest_and_subject():
    ce = _ce_v3()
    plain = _ratify(ce)
    with_trace = _ratify(ce, traceability={"consumer_models": ["M1"], "consumer_data_products": ["DP1"]})
    # la trazabilidad no entra ni en el digest contractual ni en el subject ratificado
    assert plain["evidence_ref"]["digest"] == with_trace["evidence_ref"]["digest"]
    assert "traceability" not in with_trace["subject"]
    # no cambia el estado derivado (no concede validez por consumidores)
    now = _now("2026-10-18T00:00:00Z"); dg = gd.contract_evidence_digest(ce)
    assert gd.derive_status([plain], dg, now) == gd.derive_status([with_trace], dg, now)


def test_status_revoked_dominates_and_preserves_original():
    ce = _ce_v3()
    ev = _ratify(ce)
    rev = gd.build_revocation(event_id="evt-2", subject=SUBJECT, revokes_event_id="evt-1", rationale="error",
                              actor="joconnor@danwor.com", decided_at="2026-10-18T09:00:00+00:00")
    st = gd.derive_status([ev, rev], gd.contract_evidence_digest(ce), _now("2026-10-19T00:00:00Z"))
    assert st["decision"] == "REVOKED" and st["principal"] == "REVOKED"
    assert st["evidence"] == "NA" and st["review"] == "NA"          # sin ratificación vigente


def test_status_reratification_after_revocation():
    ce = _ce_v3()
    ev = _ratify(ce)
    rev = gd.build_revocation(event_id="evt-2", subject=SUBJECT, revokes_event_id="evt-1", rationale="error",
                              actor="a@d.com", decided_at="2026-10-18T09:00:00+00:00")
    ev3 = _ratify(ce, event_id="evt-3", decided_at="2026-10-19T09:00:00+00:00")
    st = gd.derive_status([ev, rev, ev3], gd.contract_evidence_digest(ce), _now("2026-10-20T00:00:00Z"))
    assert st["decision"] == "RATIFIED" and st["ratified_event_id"] == "evt-3" and st["principal"] == "RATIFIED"


def test_derive_status_requires_aware_now():
    ev = _ratify(_ce_v3())
    with pytest.raises(gd.ValidationError):
        gd.derive_status([ev], None, datetime(2026, 10, 18))        # naive


def test_no_io_imports_only_stdlib():
    import inspect
    src = inspect.getsource(gd)
    for forbidden in ("open(", "requests", "boto3", "remote_store", "durable_backup", "assessment_service",
                      "methodological_judgment", "datetime.now", "os.environ", "random"):
        assert forbidden not in src, forbidden
