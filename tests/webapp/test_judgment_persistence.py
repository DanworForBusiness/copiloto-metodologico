"""Gate 8D.2 — el Juicio metodológico DETERMINISTA se persiste como estado DERIVADO del sobre gobernado (una sola
fuente: ``evaluate_judgment(profile_json)``), con backfill idempotente sin reassess, lectura por el estado gobernado y
presentación en español. Pruebas LEANAS del vertical nuevo (no se repiten los golden de calibración)."""
from __future__ import annotations

import gzip
import json
import os.path
from types import SimpleNamespace

import pytest

from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401 (fixtures reutilizadas)
from tools.catalog.methodological_judgment import JUDGMENT_SCHEMA_VERSION, RULESET_VERSION, evaluate_judgment
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_presenter as P
from webapp.services import assessment_service as svc

_FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "methodological_judgment")


def _golden(name):
    return json.loads(gzip.open(os.path.join(_FIX, f"{name}.json.gz")).read().decode("utf-8"))


def _env(world):
    return svc._load_envelope("Acme", "4AM_ACME")


# ═══════════════ persistencia ═══════════════
def test_generate_persists_judgment_derived_from_profile(world):
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    env = _env(world)
    assert env is not None and env.get("judgment") is not None
    assert env["judgment"]["output_signature"] == evaluate_judgment(env["profile_json"])["output_signature"]
    assert svc.judgment_versions_of(env["judgment"]) == (JUDGMENT_SCHEMA_VERSION, RULESET_VERSION)
    assert svc.status_for_entry(world.acme).judgment["output_signature"] == env["judgment"]["output_signature"]


def test_reassess_regenerates_judgment_with_profile(world):
    svc.generate(world.acme)
    svc.generate(world.acme, force=True)
    env = _env(world)
    assert env["judgment"]["output_signature"] == evaluate_judgment(env["profile_json"])["output_signature"]


def test_ui_never_recomputes__judgment_is_read_from_persisted_envelope(world):
    svc.generate(world.acme)
    st = svc.status_for_entry(world.acme)
    assert st.judgment is _env(world)["judgment"]                      # MISMO objeto persistido, no un recálculo


# ═══════════════ backfill idempotente, sin reassess ═══════════════
def test_backfill_judgment_missing_apply_idempotent_and_no_profile_mutation(world):
    svc.generate(world.acme)
    base = dict(_env(world))
    sig0, prof0, pool0 = base["assessment_artifact_signature"], base["profile_json"], base["input_pool_signature"]
    svc.apply_judgment_backfill(base, None)                           # simula un sobre histórico sin Juicio

    dry = bf.backfill_judgment(apply=False, client="Acme")
    rec = next(r for r in dry["records"] if r["terminal_am"] == "4AM_ACME")
    assert rec["action"] == "would_update" and dry["counts"]["would_update"] >= 1 and dry["counts"]["updated"] == 0

    app = bf.backfill_judgment(apply=True, client="Acme")
    rec = next(r for r in app["records"] if r["terminal_am"] == "4AM_ACME")
    assert rec["action"] == "updated" and app["counts"]["failed"] == 0
    env = _env(world)
    assert env["judgment"]["output_signature"] == evaluate_judgment(env["profile_json"])["output_signature"]
    assert env["assessment_artifact_signature"] == sig0 and env["profile_json"] == prof0 and env["input_pool_signature"] == pool0
    assert len(files(world)) == 1                                     # misma identidad/ruta, sin duplicados

    again = bf.backfill_judgment(apply=True, client="Acme")
    rec = next(r for r in again["records"] if r["terminal_am"] == "4AM_ACME")
    assert rec["action"] == "judgment_current" and again["counts"]["updated"] == 0 and again["counts"]["complete"]


def test_backfill_judgment_current_is_noop(world):
    svc.generate(world.acme)                                          # generate ya persiste el Juicio vigente
    rep = bf.backfill_judgment(apply=True, client="Acme")
    rec = next(r for r in rep["records"] if r["terminal_am"] == "4AM_ACME")
    assert rec["action"] == "judgment_current" and rep["counts"]["updated"] == 0


def test_backfill_judgment_stale_version_updates_only_judgment(world, monkeypatch):
    svc.generate(world.acme)
    base = dict(_env(world))
    stale = dict(base["judgment"]); stale["ruleset_version"] = "judgment-rules/v0"  # contrato de Juicio ANTERIOR
    svc.apply_judgment_backfill(base, stale)
    dry = bf.backfill_judgment(apply=False, client="Acme")
    rec = next(r for r in dry["records"] if r["terminal_am"] == "4AM_ACME")
    assert rec["action"] == "would_update" and dry["counts"]["judgment_version_stale"] >= 1
    bf.backfill_judgment(apply=True, client="Acme")
    env = _env(world)
    assert svc.judgment_versions_of(env["judgment"]) == (JUDGMENT_SCHEMA_VERSION, RULESET_VERSION)
    assert env["profile_json"] == base["profile_json"]               # el perfil no se tocó


# ═══════════════ lectura / compatibilidad ═══════════════
def test_compat_fallback_derives_judgment_readonly_when_absent(world):
    svc.generate(world.acme)
    env = dict(_env(world)); env.pop("judgment", None)
    jv = svc._persisted_judgment(env)
    assert jv is not None and jv["output_signature"] == evaluate_judgment(env["profile_json"])["output_signature"]


# ═══════════════ presentación (UI view-model) ═══════════════
def test_presenter_renders_buena_with_strengths_and_four_contracts():
    jv = P.judgment_view(evaluate_judgment(_golden("kensing_gl_line_item_raw")))
    assert jv["determined"] and jv["level_label"] == "Buena" and jv["tone"] == "plena"
    assert jv["strengths"] and {c["label"] for c in jv["contracts"]} == {"Ingesta", "Armonización", "Propagación", "Contrato publicado"}
    assert all(c["state"] == "positive" for c in jv["contracts"])


def test_presenter_renders_not_determinable_with_translated_reason():
    jv = P.judgment_view(evaluate_judgment(_golden("biogenesis_sellout_gp")))
    assert not jv["determined"] and jv["level_label"] == "No determinable"
    assert jv["nd_reason"] == "Clase de la fuente externa sin determinar"
    assert jv["nd_reason_id"] == "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"       # id crudo solo como metadato


def test_presenter_renders_parcial_reasons():
    jv = P.judgment_view(evaluate_judgment(_golden("biogenesis_base_stocks")))
    assert jv["determined"] and jv["level_label"] == "Parcial" and jv["reasons"]
    assert all(r["text"] and r["kind"] for r in jv["reasons"])


def test_report_view_exposes_judgment_and_freshness_separately(world):
    svc.generate(world.acme)
    cur = svc.status_for_entry(world.acme)                            # perfil REAL completo (renderizable)
    stale = svc.AssessmentStatus(svc.STALE, cur.entry_id, cur.client, cur.terminal_am, profile=cur.profile,
                                 judgment=cur.judgment, stale_reason=svc.STALE_INPUT_CHANGED)
    v = P.report_view(stale, world.acme, "datasphere_core")
    assert v["state"] == svc.STALE and v["judgment"] is not None      # el Juicio se expone separado de la vigencia (STALE no lo oculta)
    assert v["judgment"]["level_label"] == P.judgment_view(cur.judgment)["level_label"]
