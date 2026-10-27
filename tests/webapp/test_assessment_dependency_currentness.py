"""P1 · granularidad de la vigencia del assessment (sin tocar reglas metodológicas).

Contrato (criterio del dueño):
  * AFECTADO (cambió algo que el AM lee)      → STALE explícito (``stale_dependency == AFFECTED``) hasta regenerar; nunca un falso CURRENT.
  * NO afectado, independencia demostrada      → conserva su vigencia (CURRENT con ``pool_drift``), aunque la firma del pool completo del cliente cambió.
  * dependencias INCIERTAS (sobre sin firma)   → STALE explícito (``stale_dependency == UNCERTAIN``): no se afirma independencia.
La independencia se demuestra contra el motor REAL: el perfil del AM no afectado es idéntico (salvo las anclas a la firma del pool).
"""
from __future__ import annotations

import json

from tests.webapp.test_assessment_service import ACME, entry, env  # noqa: F401
from tools.catalog.modeling_assessment import shadow
from webapp.services import assessment_service as svc


def _scrub(node):
    if isinstance(node, dict):
        return {k: (None if k == "pool_signature" else _scrub(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    return node


def _profile(env_):
    doc, _ = shadow.assess(env_.inbound, "Acme", "4AM_ACME", "General")
    return json.dumps(_scrub(doc), sort_keys=True)


def _edit(path, fn):
    body = json.loads(path.read_text(encoding="utf-8"))
    fn(body)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_a_change_in_another_model_that_the_am_does_not_read_keeps_it_current_and_traceable(env):             # noqa: F811
    e = entry("Acme", ACME)
    assert svc.generate(e).state == svc.CURRENT
    before = _profile(env)
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    st = svc.status_for_entry(e)
    assert st.state == svc.CURRENT and st.pool_drift and st.stale_dependency is None
    assert _profile(env) == before                                                                            # independencia DEMOSTRADA con el motor real


def test_a_change_in_an_object_the_am_reads_is_stale_and_marked_affected(env):                                # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/views/1VR_RAW.json", lambda b: b["definitions"]["1VR_RAW"]["elements"].update({"C_NUEVA": {"type": "cds.String"}}))
    st = svc.status_for_entry(e)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_INPUT_CHANGED and st.stale_dependency == svc.DEP_AFFECTED and not st.pool_drift


def test_a_shared_dependency_changed_by_the_other_model_affects_this_one_too(env):                            # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/views/3VF_P.json", lambda b: b["definitions"]["3VF_P"]["elements"].update({"Z": {"type": "cds.String"}}))
    assert svc.status_for_entry(e).stale_dependency == svc.DEP_AFFECTED


def test_regenerating_the_affected_am_restores_current_and_stores_the_dependency_signature(env):             # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/views/1VR_RAW.json", lambda b: b["definitions"]["1VR_RAW"]["elements"].update({"C_NUEVA": {"type": "cds.String"}}))
    st = svc.generate(e)
    assert st.state == svc.CURRENT and not st.pool_drift
    stored = svc._load_envelope("Acme", "4AM_ACME")
    assert stored["input_closure_signature"] and stored["dependency_rule_version"]


def test_an_envelope_without_a_dependency_signature_is_uncertain_not_independent(env):                       # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    stored = svc._load_envelope("Acme", "4AM_ACME")
    path = svc.governed_path("Acme", "4AM_ACME")
    legacy = {k: v for k, v in stored.items() if k not in ("input_closure_signature", "dependency_rule_version")}
    path.write_text(json.dumps(legacy), encoding="utf-8")                                                     # sobre previo a esta firma (todos los de producción hoy)
    svc.reset_memory_cache()
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    st = svc.status_for_entry(e)
    assert st.state == svc.STALE and st.stale_dependency == svc.DEP_UNCERTAIN                                  # nunca se afirma independencia sin la firma


def test_uncomputable_dependencies_fall_back_to_the_pool_signature(env, monkeypatch):                          # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    monkeypatch.setattr(svc.ma_dependencies, "dependency_signatures", lambda *a, **k: (_ for _ in ()).throw(OSError("pool ilegible")))
    svc.invalidate_pool_index()
    st = svc.status_for_entry(e)
    assert st.state == svc.STALE and st.stale_dependency == svc.DEP_UNCERTAIN


def test_a_version_change_is_never_masked_by_dependency_independence(env, monkeypatch):                       # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    real = svc.current_assessment_versions()
    monkeypatch.setattr(svc, "current_assessment_versions", lambda: {**real, "ruleset": "otra-version"})
    st = svc.status_for_entry(e)
    assert st.state == svc.STALE and st.stale_reason == svc.STALE_VERSION_ONLY and st.pool_drift          # la entrada es independiente, pero el contrato metodológico cambió: sigue STALE


def test_reading_the_status_never_runs_the_assessment_engine(env, monkeypatch):                                # noqa: F811
    e = entry("Acme", ACME)
    svc.generate(e)
    _edit(env.inbound / "Acme/analytic-models/4AM_OTHER.json", lambda b: b["definitions"]["4AM_OTHER"]["elements"].update({"Z": {"type": "cds.String"}}))
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(AssertionError("el motor NO debe ejecutarse al leer")))
    assert svc.status_for_entry(e).state == svc.CURRENT
