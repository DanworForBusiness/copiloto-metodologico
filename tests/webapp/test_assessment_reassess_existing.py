"""test_assessment_reassess_existing.py — modo ``assessment_backfill --reassess-existing``: reconstrucción COMPLETA y OFFLINE de los assessments gobernados
EXISTENTES (CURRENT + STALE) con el MISMO ``svc.generate(entry, force=True)``, en la MISMA identidad gobernada; sin Datasphere/DSP, sin publicación, sin
segundo motor. Confía en el pool LOCAL persistido (no afirma que Datasphere esté al día). Fixtures reutilizadas del test de durabilidad."""
from __future__ import annotations

import inspect
import json
import socket
from types import SimpleNamespace

import pytest

from tests.webapp.test_assessment_reassess import Gen, _entry, _run as _run_current, _st  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart, world  # noqa: F401 (fixtures reutilizadas)
from tools.catalog.domain_classification import DOMAIN_CLASSIFICATION_VERSION
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc


def _run(entries, statuses, gen, apply):
    return bf.reassess_existing(entries, apply=apply, status_for=lambda e: statuses[e.entry_id], generate=gen,
                                governed_path=lambda c, a: f"/gov/{c.lower()}/{a}.json", retry_remote=lambda c, a: True,
                                governed_state_present=lambda: True, remote_enabled=lambda: True,
                                load_envelope=lambda c, a: None, store_envelope=lambda e: svc.DURABLE_REMOTE)


def _make_stale(world):
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A8"', 1), encoding="utf-8")
    restart()


# ── elegibilidad (dobles) ───────────────────────────────────────────────────
def test_current_and_stale_governed_assessments_are_included_with_the_per_root_contract():
    e1, e2 = _entry("e1", "Acme", "AM_A"), _entry("e2", "Acme", "AM_B")
    gen = Gen()
    rep = _run([e1, e2], {"e1": _st(e1, svc.CURRENT, "AM_A"), "e2": _st(e2, svc.STALE, "AM_B")}, gen, apply=False)
    assert rep["mode"] == "reassess-existing" and rep["dry_run"] is True
    recs = {r["state_before"]: r for r in rep["records"]}
    assert set(recs) == {svc.CURRENT, svc.STALE}
    for r in rep["records"]:
        assert r["action"] == bf.WOULD_REASSESS_EXISTING and r["eligible"] is True and r["governed_path"].startswith("/gov/acme/") and r["client"] == "Acme" and r["root"]
    c = rep["counts"]
    assert (c["eligible"], c["current"], c["stale"], c["would_reassess"], c["skipped"], c["failed"]) == (2, 1, 1, 2, 0, 0)


def test_roots_without_a_governed_assessment_are_excluded():
    es = [_entry(f"e{i}", "Acme", f"AM_{i}") for i in range(3)]
    sts = {"e0": _st(es[0], svc.NOT_YET_ASSESSED, "AM_0"), "e1": _st(es[1], svc.NOT_APPLICABLE, "AM_1"), "e2": _st(es[2], svc.FAILED, "AM_2")}
    gen = Gen()
    rep = _run(es, sts, gen, apply=True)
    assert gen.calls == [] and rep["counts"]["eligible"] == 0 and rep["counts"]["skipped"] == 3 and rep["counts"]["reassessed"] == 0
    assert {r["reason"] for r in rep["records"]} == {bf.SKIPPED_NOT_EXISTING} and all(r["action"] == bf.SKIP for r in rep["records"])


def test_dry_run_calls_nothing_and_apply_calls_the_canonical_forced_generation_sequentially():
    e1, e2 = _entry("e1", "Acme", "AM_A"), _entry("e2", "Acme", "AM_B")
    sts = {"e1": _st(e1, svc.CURRENT, "AM_A"), "e2": _st(e2, svc.STALE, "AM_B")}
    dry = Gen()
    _run([e1, e2], sts, dry, apply=False)
    assert dry.calls == []                                                          # dry-run: CERO escrituras / CERO generación
    gen = Gen()
    rep = _run([e1, e2], sts, gen, apply=True)
    assert gen.calls == [("e1", True), ("e2", True)]                                # orden secuencial; force=True canónico
    assert rep["counts"]["reassessed"] == 2 and {r["action"] for r in rep["records"]} == {bf.REASSESSED_EXISTING}
    assert rep["complete"] is True and all(r["identity_unchanged"] for r in rep["records"])


def test_one_failing_root_does_not_stop_the_others():
    e1, e2 = _entry("e1", "Acme", "AM_A"), _entry("e2", "Acme", "AM_B")
    sts = {"e1": _st(e1, svc.STALE, "AM_A"), "e2": _st(e2, svc.CURRENT, "AM_B")}

    def gen(e, *, force=False):
        if e.entry_id == "e1":
            raise RuntimeError("boom")
        return svc.AssessmentStatus(svc.CURRENT, e.entry_id, e.cliente, "AM_B", durable=svc.DURABLE_REMOTE)
    rep = _run([e1, e2], sts, gen, apply=True)
    assert [r["action"] for r in rep["records"]] == [bf.FAILED_ACTION, bf.REASSESSED_EXISTING] and rep["counts"]["failed"] == 1 and rep["complete"] is False


# ── reconstrucción real (pool + governed en tmp) ────────────────────────────
def test_stale_governed_assessment_is_rebuilt_in_the_same_identity_with_judgment_and_domain_classification(world, monkeypatch):
    svc.generate(world.acme)
    path = files(world)[0]
    env = json.loads(path.read_text(encoding="utf-8"))
    env.pop("judgment"), env.pop("domain_classification")                           # sobre histórico (anterior a Juicio persistido y a la clasificación)
    path.write_text(json.dumps(env), encoding="utf-8")
    _make_stale(world)
    assert svc.status_for_entry(world.acme).state == svc.STALE
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sin red")))     # sin adquisición remota
    dry = bf.reassess_existing([world.acme], apply=False)
    assert dry["counts"]["would_reassess"] == 1 and json.loads(path.read_text(encoding="utf-8")) == env                 # dry-run: bytes sin cambios
    rep = bf.reassess_existing([world.acme], apply=True)
    assert rep["counts"]["reassessed"] == 1 and rep["counts"]["failed"] == 0 and rep["records"][0]["identity_unchanged"] is True
    assert files(world) == [path]                                                   # MISMA identidad/ruta; sin duplicados
    new = json.loads(path.read_text(encoding="utf-8"))
    assert (new["client"], new["terminal_am"]) == (env["client"], env["terminal_am"])
    assert new["judgment"] == svc._derive_judgment(new["profile_json"])             # Juicio regenerado por el motor canónico
    assert new["domain_classification"]["version"] == DOMAIN_CLASSIFICATION_VERSION  # clasificación regenerada
    assert svc.status_for_entry(world.acme).state == svc.CURRENT                    # vigencia recalculada canónicamente (no forzada)


def test_a_failed_regeneration_restores_the_previous_governed_envelope(world, monkeypatch):
    svc.generate(world.acme)
    path = files(world)[0]
    _make_stale(world)
    before = json.loads(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("motor caído")))
    rep = bf.reassess_existing([world.acme], apply=True)
    rec = rep["records"][0]
    assert rec["action"] == bf.FAILED_ACTION and rep["counts"]["failed"] == 1 and rec["previous_envelope_restored"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == before                   # el sobre anterior quedó EXACTAMENTE como estaba (no un FAILED encima)
    restart()
    assert svc.status_for_entry(world.acme).state == svc.STALE


# ── sin DSP / adquisición; CLI ──────────────────────────────────────────────
def test_the_operator_never_touches_acquisition_publication_or_a_second_engine():
    src = inspect.getsource(bf.reassess_existing) + inspect.getsource(bf._reassess_core)
    for forbidden in ("dsp", "datasphere_publication", "acquire", "inventory", "publish", "_engine_assess", "regenerate_in_memory", "profile_json", "state=CURRENT", "state = CURRENT"):
        assert forbidden not in src, forbidden
    assert "generate(entry, force=True)" in src


def test_existing_cli_modes_keep_their_semantics_and_the_new_mode_is_explicit(world, capsys):
    for argv in (["--reassess-current", "--dry-run"],                               # reassess-current sigue exigiendo --client
                 ["--reassess-current", "--client", "Acme"],                        # y dry-run/apply
                 ["--reassess-existing"],                                           # el modo nuevo también exige dry-run/apply
                 ["--reassess-existing", "--dry-run", "--apply"],
                 ["--reassess-existing", "--reassess-current", "--dry-run"],       # modos mutuamente excluyentes
                 ["--backfill-domain-classification"], ["--backfill-judgment"]):
        with pytest.raises(SystemExit):
            bf.main(argv)
    assert bf.main(["--reassess-existing", "--dry-run"]) in (0, 2)                  # sin --client: todos los existentes
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "reassess-existing" and out["dry_run"] is True and {"eligible", "current", "stale", "would_reassess", "skipped", "failed"} <= set(out["counts"])
    assert bf.main(["--reassess-existing", "--client", "Acme", "--dry-run"]) in (0, 2)
    assert json.loads(capsys.readouterr().out)["mode"] == "reassess-existing"
    # --reassess-current sigue SIN reevaluar STALE
    e = _entry("e1", "Acme", "AM_A")
    rep = _run_current([e], {"e1": _st(e, svc.STALE, "AM_A")}, Gen(), apply=True)
    assert rep["counts"]["reassessed"] == 0 and rep["records"][0]["reason"] == bf.SKIPPED_NOT_CURRENT and rep["mode"] == "reassess-current"
