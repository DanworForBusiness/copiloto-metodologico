"""P1 · el informe explica POR QUÉ un assessment está vigente o desactualizado ante un cambio del catálogo del cliente (sin tocar el contenido metodológico del informe)."""
from __future__ import annotations

import json

from tests.webapp.test_assessment_service import ACME, entry, env  # noqa: F401
from webapp.services import assessment_presenter as P
from webapp.services import assessment_service as svc


def _edit(path, fn):
    body = json.loads(path.read_text(encoding="utf-8"))
    fn(body)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_a_current_assessment_kept_by_independence_says_so_and_a_regular_one_does_not(env):          # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    assert P.report_view(svc.status_for_entry(e), e, "pack")["drift_note"] is None
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    view = P.report_view(svc.status_for_entry(e), e, "pack")
    assert view["state"] == "CURRENT" and view["drift_note"] == P.DRIFT_NOTE and view["state_note"] is None


def test_an_affected_assessment_says_that_something_it_uses_changed(env):                           # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/views/1VR_RAW.json", lambda b: b["definitions"]["1VR_RAW"]["elements"].update({"C": {"type": "cds.String"}}))
    view = P.report_view(svc.status_for_entry(e), e, "pack")
    assert view["state"] == "STALE" and view["stale_dependency"] == svc.DEP_AFFECTED
    assert view["state_note"] == P.STALE_DEPENDENCY_NOTES[svc.DEP_AFFECTED] and view["state_body"] == P.STALE_DEPENDENCY_BODIES[svc.DEP_AFFECTED] and view["drift_note"] is None


def test_an_uncertain_assessment_never_claims_the_model_changed(env):                               # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    path = svc.governed_path("Acme", "4AM_ACME")
    legacy = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k not in ("input_closure_signature", "dependency_rule_version")}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    svc.reset_memory_cache()
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    view = P.report_view(svc.status_for_entry(e), e, "pack")
    assert view["state"] == "STALE" and view["stale_dependency"] == svc.DEP_UNCERTAIN and "cambió" not in view["state_note"]
    assert view["state_note"] == P.STALE_DEPENDENCY_NOTES[svc.DEP_UNCERTAIN]


def test_version_only_and_unknown_causes_keep_their_historic_wording(env):                          # noqa: F811
    st = svc.AssessmentStatus(svc.STALE, "x", "Acme", "AM", stale_reason=svc.STALE_VERSION_ONLY)
    assert P.stale_note(st) == P.STALE_NOTES[svc.STALE_VERSION_ONLY]
    assert P.stale_note(svc.AssessmentStatus(svc.STALE, "x", "Acme", "AM")) == P.STATE_NOTES[svc.STALE]
