"""test_assessment_reassess.py — modo ``assessment_backfill --reassess-current``: reevaluación EXPLÍCITA de assessments gobernados
CURRENT con el MISMO motor canónico de generación (``svc.generate(entry, force=True)``), reemplazando la MISMA identidad gobernada.
Sin migración, sin parche de JSON, sin borrado, sin duplicados. Fixtures reutilizadas del test de durabilidad (pool + governed reales
en tmp)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401 (fixtures reutilizadas)
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc


# ═══════════════ servicio: force reevalúa un CURRENT por el motor canónico ═══════════════
def test_generate_force_reassesses_a_current_profile_via_canonical_engine(world, monkeypatch):
    assert svc.generate(world.acme).state == svc.CURRENT
    assert svc.status_for_entry(world.acme).state == svc.CURRENT
    calls: list = []
    orig = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (calls.append(a), orig(*a, **k))[1])
    assert svc.generate(world.acme).state == svc.CURRENT and calls == []            # sin force: idempotente, el motor NO corre
    r = svc.generate(world.acme, force=True)                                        # con force: corre el motor canónico de nuevo
    assert r.state == svc.CURRENT and r.durable in (svc.DURABLE_REMOTE, svc.DURABLE_LOCAL) and len(calls) == 1
    assert len(files(world)) == 1                                                   # MISMA identidad/ruta: sin duplicados


def test_default_backfill_still_skips_current(world):
    svc.generate(world.acme)
    rep = bf.backfill(world.entries, dry_run=True)
    assert not any(r["client"] == "Acme" for r in rep["generated"])                 # backfill normal jamás regenera un CURRENT
    assert any(r["client"] == "Acme" and r["state"] != svc.NOT_YET_ASSESSED for r in rep["skipped"])


# ═══════════════ backfill: contrato de reassess_selected con dobles ═══════════════
def _entry(eid, cliente="Acme", am="AM_A"):
    return SimpleNamespace(entry_id=eid, cliente=cliente, dominio="Dom", artefactos=[am], path="x")


def _st(e, state, am="AM_A"):
    return svc.AssessmentStatus(state, e.entry_id, e.cliente, am if state != svc.NOT_APPLICABLE else None)


class Gen:
    """Doble de ``svc.generate`` que registra (entry_id, force)."""
    def __init__(self, durable=svc.DURABLE_REMOTE, state=svc.CURRENT):
        self.calls: list = []
        self.durable, self.state = durable, state

    def __call__(self, e, *, force=False):
        self.calls.append((e.entry_id, force))
        return svc.AssessmentStatus(self.state, e.entry_id, e.cliente, "AM_A", durable=self.durable)


def _run(entries, statuses, gen, apply):
    return bf.reassess_selected(entries, apply=apply, status_for=lambda e: statuses[e.entry_id], generate=gen,
                                governed_path=lambda c, a: f"/gov/{c.lower()}/{a}.json", retry_remote=lambda c, a: True,
                                governed_state_present=lambda: True, remote_enabled=lambda: True)


def test_dry_run_selects_current_and_writes_nothing():
    e = _entry("1"); gen = Gen()
    rep = _run([e], {"1": _st(e, svc.CURRENT)}, gen, apply=False)
    rec = rep["records"][0]
    assert rec["action"] == "WOULD_REASSESS" and rec["governed_path"] == "/gov/acme/AM_A.json"
    assert rep["counts"]["would_reassess"] == 1 and rep["counts"]["reassessed"] == 0
    assert gen.calls == []                                                          # dry-run: CERO llamadas al motor, CERO escrituras


def test_apply_reassesses_current_via_canonical_generate_force():
    e = _entry("1"); gen = Gen()
    rep = _run([e], {"1": _st(e, svc.CURRENT)}, gen, apply=True)
    assert gen.calls == [("1", True)]                                              # usa el camino canónico: generate(force=True)
    rec = rep["records"][0]
    assert rec["action"] == "REASSESSED" and rec["durable"] == svc.DURABLE_REMOTE and rec["identity_unchanged"] is True
    assert rep["counts"]["reassessed"] == 1 and rep["counts"]["failed"] == 0 and rep["restart_safe"] is True


@pytest.mark.parametrize("state", ["NOT_YET_ASSESSED", "STALE", "NOT_APPLICABLE"])
def test_non_current_states_are_skipped_not_reassessed(state):
    e = _entry("1"); gen = Gen()
    rep = _run([e], {"1": _st(e, getattr(svc, state))}, gen, apply=True)
    assert rep["records"][0]["reason"] == "skipped_not_current" and gen.calls == []


def test_dedupe_by_client_and_terminal_am_no_duplicate():
    e1, e2 = _entry("1"), _entry("2"); gen = Gen()                                  # dos entradas de Catálogo -> el MISMO (cliente, AM)
    rep = _run([e1, e2], {"1": _st(e1, svc.CURRENT), "2": _st(e2, svc.CURRENT)}, gen, apply=True)
    assert len(rep["records"]) == 1 and gen.calls == [("1", True)]                  # una sola reevaluación, sin duplicados


def test_client_scoping_does_not_touch_other_clients():
    ea, eb = _entry("1", "Kensing"), _entry("2", "Biogenesis"); gen = Gen()
    scoped = [e for e in [ea, eb] if e.cliente.lower() == "kensing"]                # el main() filtra por --client; acá simulamos el alcance
    rep = _run(scoped, {"1": _st(ea, svc.CURRENT)}, gen, apply=True)
    assert [c[0] for c in gen.calls] == ["1"] and all(r["client"] == "Kensing" for r in rep["records"])


def test_reassess_uses_canonical_generation_not_migration_nor_json_patch():
    src = inspect.getsource(bf._reassess_core)                                     # núcleo compartido de --reassess-current
    assert "generate(entry, force=True)" in src
    assert "regenerate_stale_evidence" not in src and "commit_stale_migration" not in src
    assert "profile_json" not in src and "EVIDENCE_MIGRATIONS" not in src


def test_cli_requires_explicit_client_for_reassess():
    with pytest.raises(SystemExit):
        bf.main(["--reassess-current", "--dry-run"])                               # sin --client: error (no reevalúa todos los clientes)
    with pytest.raises(SystemExit):
        bf.main(["--reassess-current", "--client", "Kensing"])                     # sin --dry-run/--apply: error
