"""``secured_migrate``: ABSTENCIONES CONOCIDAS vs. abstenciones/fallas nuevas (hotfix de ``signals_layer_boundary_v4`` en produccion).

Defecto: una ola se detenia ante CUALQUIER abstencion, aunque el objeto ya estuviera fuera del plan firmado y el operador lo hubiera visto en el dry-run. Contrato nuevo:
  * ``--dry-run`` informa ``abstentions`` (cliente, AM, causa, sha256 del sobre) y su ``digest``; con ``known_abstentions=<digest>`` ESE conjunto exacto deja de detener las olas (los abstenidos jamas se escriben);
  * abstencion nueva / otra causa, falla, divergencia del plan, objeto abstenido modificado, firma o respaldo invalidos, escritura remota no confirmada => se detiene;
  * sin el digest, el comportamiento original (cualquier abstencion detiene) no cambia;
  * el resultado distingue ``APPLIED`` (``completion=COMPLETE``) de ``APPLIED_WITH_ABSTENTIONS`` (``completion=INCOMPLETE_PENDING_ABSTENTIONS``): jamas se declara completa una migracion con pendientes.

Todo sobre COPIAS aisladas (``tmp_path``; Spaces FALSO en memoria): nunca estado real."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from core import remote_store
from tests.webapp.test_assessment_enrichment import ALWAYS
from tests.webapp.test_assessment_layer_boundary_migration import MIG, _BACKUPS, _env, _grant, _make_v3, _path, _rec, _snapshot, lworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import FakeBucket, files, restart
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma

NONLB, CONF, DRIFT = "NON_LAYER_BOUNDARY_CONTENT_CHANGED", "CONFIDENCE_LEVEL_CHANGED", "CLOSURE_DRIFT"


class Engine:
    """``regenerate`` de prueba: ``spec = {cliente: [causa_en_llamada_1, causa_en_llamada_2, ...]}`` (``None`` = regenera normal; se repite la ultima). La llamada 1 es el PLAN (dry-run interno), la 2 la OLA.
    ``hook(cliente, n)`` corre antes de decidir (para simular cambios concurrentes)."""

    def __init__(self, spec: dict, hook=None):
        self.spec, self.hook, self.calls = spec, hook, {}
        self.real = svc.regenerate_stale_evidence

    def __call__(self, entry, migration):
        n = self.calls[entry.cliente] = self.calls.get(entry.cliente, 0) + 1
        if self.hook:
            self.hook(entry.cliente, n)
        seq = self.spec.get(entry.cliente) or [None]
        code = seq[min(n - 1, len(seq) - 1)]
        if code:
            raise svc.EnrichmentError(code)
        return self.real(entry, migration)


def _dry(w, engine):
    return bf.migrate_stale_evidence(list(w.entries), MIG, apply=False, governed_state_present=ALWAYS, regenerate=engine)


def _digest_for(w, spec) -> str:
    return _dry(w, Engine(spec))["abstentions"]["digest"]


def _apply(w, engine, *, ack=None, waves=None, backup=None, authorizer=_grant, **kw):
    return bf.secured_migrate(list(w.entries), MIG, authorizer=authorizer, backup_dir=backup if backup is not None else w.tmp / f"kbk-{next(_BACKUPS)}", allow_local_only=kw.pop("allow_local_only", True),
                              governed_state_present=ALWAYS, regenerate=engine, known_abstentions=ack, waves=waves, **kw)


def _bytes(w, client) -> bytes:
    return _path(w, client).read_bytes()


# ───────────────────────────── el dry-run informa las abstenciones conocidas ─────────────────────────────
def test_the_dry_run_reports_the_known_abstentions_with_identity_cause_and_envelope_bytes_and_writes_nothing(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    snap = _snapshot(lworld)
    rep = _dry(lworld, Engine({"Bcme": [NONLB]}))
    ab = rep["abstentions"]
    assert ab["count"] == 1 and [(i["client"], i["reason"]) for i in ab["items"]] == [("Bcme", NONLB)] and ab["items"][0]["sha256"] and ab["digest"]
    assert len(rep["plan"]["items"]) == 1 and rep["counts"]["abstained"] == 1 and rep["counts"]["would_regenerate"] == 1 and _snapshot(lworld) == snap
    assert _digest_for(lworld, {"Bcme": [NONLB]}) == ab["digest"]                                                    # estable
    assert _digest_for(lworld, {"Bcme": [CONF]}) != ab["digest"] and _digest_for(lworld, {}) != ab["digest"]            # otra causa / otro conjunto => otro digest
    p = _path(lworld, "Bcme")
    p.write_bytes(p.read_bytes() + b" ")
    assert _digest_for(lworld, {"Bcme": [NONLB]}) != ab["digest"]                                                    # los bytes del abstenido tambien entran


# ───────────────────────────── ola mixta: autorizados + abstenciones conocidas ─────────────────────────────
def test_a_mixed_wave_applies_the_authorized_objects_and_leaves_the_known_abstentions_intact_without_declaring_completion(lworld):
    old = _make_v3(lworld, lworld.a, lworld.b)
    digest = _digest_for(lworld, {"Bcme": [NONLB]})
    b_before, snap = _bytes(lworld, "Bcme"), _snapshot(lworld)
    out = _apply(lworld, Engine({"Bcme": [NONLB]}), ack=digest, waves=[["Acme", "Bcme"]])                          # UNA ola con ambos: el defecto la detenia
    assert out["outcome"] == bf.APPLIED_WITH_ABSTENTIONS and out["outcome"] != bf.APPLIED and out["applied"] is True
    assert out["completion"] in (bf.INCOMPLETE_PENDING_ABSTENTIONS, bf.INCOMPLETE) and out["completion"] != bf.COMPLETE and out["complete"] is False       # sin Spaces tambien queda pendiente lo remoto
    assert out["counts"]["regenerated_local"] == 1 and out["counts"]["abstained"] == 1 and out["counts"]["failed"] == 0
    wave = out["secured_apply"]["waves"][0]
    assert wave["status"] == "CLEAN" and wave["abstained_known"] == 1 and wave["abstained_unexpected"] == 0
    assert _bytes(lworld, "Bcme") == b_before                                                                        # el abstenido, byte a byte intacto
    assert svc.status_for_entry(lworld.a).pending_migration is None and svc.status_for_entry(lworld.b).pending_migration == MIG
    assert {k: v for k, v in _snapshot(lworld).items() if "Acme" not in k} == {k: v for k, v in snap.items() if "Acme" not in k}      # nada fuera del alcance
    new = _env(_path(lworld, "Acme"))
    jo, jn = old["Acme"]["judgment"], new["judgment"]
    assert svc.judgment_methodology_diff(jo, jn) == [] and (jn["level"], jn["level_rule"]) == (jo["level"], jo["level_rule"])        # Juicio v4 y nivel intactos
    assert any(w.startswith("MIGRATION_NOT_COMPLETE") for w in out["environment_warnings"]) and out["abstentions"]["count"] == 1
    bk = lworld.tmp / out["secured_apply"]["backup"]["dir"].split("\\")[-1].split("/")[-1]
    assert [o["client"] for o in gb.load_manifest(Path(out["secured_apply"]["backup"]["dir"]))["objects"]] == ["Acme"]            # el respaldo cubre SOLO lo autorizado
    ev = gb.read_journal(Path(out["secured_apply"]["backup"]["dir"]))
    assert [e["event"] for e in ev].count("COMMITTED") == 1 and bk.exists()


def test_a_clean_migration_is_still_applied_and_complete(lworld, lremote):
    _make_v3(lworld, lworld.a, lworld.b)
    _publish_all(lworld)
    out = _apply(lworld, Engine({}), allow_local_only=False)                                                         # con Spaces confirmado por bytes y sin abstenciones: COMPLETE
    assert out["outcome"] == bf.APPLIED and out["completion"] == bf.COMPLETE and out["complete"] is True and out["counts"]["abstained"] == 0 and out["abstentions"]["count"] == 0


def test_without_the_acknowledgement_any_abstention_still_stops_the_waves_as_before(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    a_before = _bytes(lworld, "Acme")
    out = _apply(lworld, Engine({"Bcme": [NONLB]}), waves=[["Bcme"], ["Acme"]])
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["completion"] == bf.INCOMPLETE and out["complete"] is False
    assert out["secured_apply"]["waves"][0]["abstained_unexpected"] == 1 and out["secured_apply"]["not_attempted_waves"] == [["Acme"]] and _bytes(lworld, "Acme") == a_before


# ───────────────────────────── el conjunto reconocido debe ser EXACTAMENTE el de ahora ─────────────────────────────
@pytest.mark.parametrize("scenario", ["bogus", "no_abstentions_now", "other_cause", "envelope_changed"])
def test_an_acknowledgement_that_does_not_match_the_current_set_is_refused_before_any_write(lworld, scenario):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    spec = {"Bcme": [NONLB]}
    if scenario == "bogus":
        ack = "0" * 64
    elif scenario == "no_abstentions_now":
        spec = {}
    elif scenario == "other_cause":
        spec = {"Bcme": [CONF]}
    elif scenario == "envelope_changed":
        p = _path(lworld, "Bcme")
        p.write_bytes(p.read_bytes() + b" ")
    snap = _snapshot(lworld)
    out = _apply(lworld, Engine(spec), ack=ack if scenario != "envelope_changed" else ack, backup=lworld.tmp / "never")
    snap = snap if scenario != "envelope_changed" else _snapshot(lworld)
    assert out["outcome"] == bf.ABSTENTIONS_CHANGED and out["applied"] is False and out["writes"] == 0 and out["complete"] is False
    assert out["abstentions"]["digest"] != ack and _snapshot(lworld) == snap and not (lworld.tmp / "never").exists()
    assert svc.status_for_entry(lworld.a).pending_migration == MIG and svc.status_for_entry(lworld.b).pending_migration == MIG


# ───────────────────────────── lo inesperado SI detiene ─────────────────────────────
@pytest.mark.parametrize("acme_seq,bcme_seq,why", [
    ([None, DRIFT], [NONLB], "new_abstention_on_an_authorized_object"),
    ([None], [NONLB, CONF], "known_object_abstains_with_another_cause"),
])
def test_an_unexpected_abstention_stops_the_waves_and_writes_nothing_more(lworld, acme_seq, bcme_seq, why):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    snap = _snapshot(lworld)
    first = ["Acme"] if why == "new_abstention_on_an_authorized_object" else ["Bcme"]
    second = ["Bcme"] if first == ["Acme"] else ["Acme"]
    out = _apply(lworld, Engine({"Acme": acme_seq, "Bcme": bcme_seq}), ack=ack, waves=[first, second])
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["completion"] == bf.INCOMPLETE and out["complete"] is False
    assert out["secured_apply"]["waves"][0]["abstained_unexpected"] == 1 and out["secured_apply"]["not_attempted_waves"] == [second]
    assert out["counts"]["regenerated_local"] == 0 and _snapshot(lworld) == snap                                      # no se escribio nada
    assert any(w.startswith("STOPPED_AT_WAVE_1") for w in out["environment_warnings"])


def test_plan_divergence_stops_even_when_the_plan_diverges_on_a_known_abstained_object(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    snap = _snapshot(lworld)
    out = _apply(lworld, Engine({"Bcme": [NONLB, None]}), ack=ack, waves=[["Bcme"], ["Acme"]])                      # el 'abstenido' ahora SI regenera: NO esta en el plan firmado
    assert out["outcome"] == bf.STOPPED_AT_WAVE and _rec(out, "Bcme")["action"] == bf.FAILED_ACTION and _rec(out, "Bcme")["reason"] == "PLAN_CHANGED"
    assert _snapshot(lworld) == snap and out["secured_apply"]["not_attempted_waves"] == [["Acme"]]


def test_a_candidate_that_differs_from_the_signed_item_is_never_written_in_a_mixed_run(lworld):
    import dataclasses
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    snap = _snapshot(lworld)
    real = Engine({"Bcme": [NONLB]})

    def shifting(entry, migration):
        cand = real(entry, migration)
        if real.calls[entry.cliente] > 1:
            cand = dataclasses.replace(cand, envelope={**cand.envelope, "assessment_artifact_signature": "0" * 64})
        return cand
    out = _apply(lworld, shifting, ack=ack, waves=[["Acme", "Bcme"]])
    assert out["outcome"] == bf.STOPPED_AT_WAVE and _rec(out, "Acme")["reason"] == "PLAN_CHANGED" and out["counts"]["failed"] == 1 and _snapshot(lworld) == snap


def test_a_known_abstained_object_that_changes_during_the_run_stops_the_waves(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})

    def tamper(client, n):
        if client == "Acme" and n == 2:                                                                               # durante la ola, alguien toca el sobre abstenido
            p = _path(lworld, "Bcme")
            p.write_bytes(p.read_bytes() + b" ")
    out = _apply(lworld, Engine({"Bcme": [NONLB]}, hook=tamper), ack=ack, waves=[["Acme"], ["Bcme"]])
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["secured_apply"]["waves"][0]["known_abstained_modified"] == ["Bcme/" + _env(_path(lworld, "Bcme"))["terminal_am"]]
    assert any(w.startswith("KNOWN_ABSTAINED_OBJECT_MODIFIED") for w in out["environment_warnings"])


# ───────────────────────────── firma, respaldo y escritura remota siguen siendo obligatorios ─────────────────────────────
def test_the_acknowledgement_never_replaces_the_signed_authorization(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    snap = _snapshot(lworld)
    for authorizer, reason in ((ma.deny_all, ma.NO_APPROVAL), (lambda m, p: ma.Authorization(False, ma.BAD_SIGNATURE), ma.BAD_SIGNATURE), (lambda m, p: ma.Authorization(False, ma.PLAN_CHANGED), ma.PLAN_CHANGED)):
        out = _apply(lworld, Engine({"Bcme": [NONLB]}), ack=ack, authorizer=authorizer, backup=lworld.tmp / "den")
        assert out["outcome"] == bf.DENIED and out["authorization"]["reason"] == reason and out["writes"] == 0 and _snapshot(lworld) == snap and not (lworld.tmp / "den").exists()


def test_a_real_signed_approval_of_the_exact_plan_is_still_required_and_verified(lworld, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv, keys, now = Ed25519PrivateKey.generate(), None, datetime.now(timezone.utc)
    keys = {"alice": priv.public_key()}
    _make_v3(lworld, lworld.a, lworld.b)
    spec = {"Bcme": [NONLB]}
    plan = _dry(lworld, Engine(spec))["plan"]
    ack = _digest_for(lworld, spec)

    def approval(plan_, name):
        f = tmp_path / name
        f.write_text(json.dumps(ma.issue_approval(migration=MIG, plan_digest=plan_["digest"], clients=plan_["clients"], approver="alice", operator="bob", private_key=priv,
                                                  issued_at=now - timedelta(minutes=1), ttl=timedelta(hours=1))), encoding="utf-8")
        return f
    snap = _snapshot(lworld)
    wrong = dict(plan, digest="e" * 64)                                                                               # firmada para OTRO plan
    out = _apply(lworld, Engine(spec), ack=ack, authorizer=ma.authorizer_from_file(approval(wrong, "bad.json"), "bob", approver_keys=keys))
    assert out["outcome"] == bf.DENIED and out["authorization"]["reason"] == ma.PLAN_CHANGED and _snapshot(lworld) == snap
    out = _apply(lworld, Engine(spec), ack=ack, authorizer=ma.authorizer_from_file(approval(plan, "ok.json"), "bob", approver_keys=keys), waves=[["Acme", "Bcme"]])
    assert out["outcome"] == bf.APPLIED_WITH_ABSTENTIONS and out["secured_apply"]["authorization"]["approver"] == "alice"
    ev = gb.read_journal(Path(out["secured_apply"]["backup"]["dir"]))
    assert [e["event"] for e in ev][0] == "BACKED_UP" or "AUTHORIZED" in [e["event"] for e in ev]


def test_a_backup_that_cannot_be_guaranteed_blocks_every_write_even_with_known_abstentions(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    snap = _snapshot(lworld)
    busy = lworld.tmp / "busy"
    busy.mkdir()
    (busy / "otro.txt").write_text("x")
    out = _apply(lworld, Engine({"Bcme": [NONLB]}), ack=ack, backup=busy)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "BACKUP_DIR_NOT_EMPTY" and out["applied"] is False and out["writes"] == 0 and _snapshot(lworld) == snap
    assert _apply(lworld, Engine({"Bcme": [NONLB]}), ack=ack, backup="")["backup_error"] == "BACKUP_DIR_REQUIRED" and _snapshot(lworld) == snap


def test_a_local_write_failure_stops_the_waves_and_a_rerun_recovers_to_applied_with_abstentions(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    ack = _digest_for(lworld, {"Bcme": [NONLB]})
    snap = _snapshot(lworld)

    def broken(candidate):
        raise OSError("disco lleno")
    out = _apply(lworld, Engine({"Bcme": [NONLB]}), ack=ack, waves=[["Acme"], ["Bcme"]], commit=broken)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and _rec(out, "Acme")["action"] == bf.FAILED_ACTION and out["completion"] == bf.INCOMPLETE and _snapshot(lworld) == snap
    again = _apply(lworld, Engine({"Bcme": [NONLB]}), ack=_digest_for(lworld, {"Bcme": [NONLB]}), waves=[["Acme", "Bcme"]])         # nuevo plan, nueva autorizacion, mismo conjunto de abstenciones
    assert again["outcome"] == bf.APPLIED_WITH_ABSTENTIONS and again["counts"]["regenerated_local"] == 1 and svc.status_for_entry(lworld.a).pending_migration is None


# ───────────────────────────── idempotencia ─────────────────────────────
def test_rerunning_after_applied_with_abstentions_is_a_no_op_that_still_reports_incomplete(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    spec = {"Bcme": [NONLB]}
    first = _apply(lworld, Engine(spec), ack=_digest_for(lworld, spec), waves=[["Acme", "Bcme"]])
    assert first["outcome"] == bf.APPLIED_WITH_ABSTENTIONS
    snap = _snapshot(lworld)
    for ack in (None, _digest_for(lworld, spec)):
        again = _apply(lworld, Engine(spec), ack=ack, backup=lworld.tmp / f"again-{ack is None}")
        assert again["outcome"] == bf.RECOVERY_ONLY and again["applied"] is False and again["counts"]["regenerated_local"] == 0 and again["counts"]["failed"] == 0
        assert again["complete"] is False and again["completion"] != bf.COMPLETE and _snapshot(lworld) == snap and not (lworld.tmp / f"again-{ack is None}").exists()
        assert any(w.startswith("MIGRATION_NOT_COMPLETE") for w in again["environment_warnings"])


# ───────────────────────────── escritura remota: confirmacion por bytes ─────────────────────────────
@pytest.fixture()
def lremote(lworld, monkeypatch):
    bucket = FakeBucket()

    def put_object(Bucket, Key, Body):
        if getattr(bucket, "fail_puts", False):
            raise RuntimeError("simulated put failure")
        bucket.objects[Key] = bytes(Body)
    bucket.put_object = put_object

    class NoSuchKey(Exception):
        response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}

    def get_object(Bucket, Key):
        if Key not in bucket.objects:
            raise NoSuchKey()
        return {"Body": SimpleNamespace(read=lambda: bucket.objects[Key])}
    bucket.get_object = get_object
    for name, value in (("SPACES_ENDPOINT", "https://fake.digitaloceanspaces.com"), ("SPACES_BUCKET", "fake-bucket"), ("SPACES_KEY", "fake-key"), ("SPACES_SECRET", "fake-secret"),
                        ("PERSIST_ENABLED", True), ("BASE_DIR", lworld.tmp), ("PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME])):
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(remote_store, "_get_client", lambda: bucket)
    monkeypatch.setattr(remote_store, "_signatures", {})
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    return bucket


def _publish_all(w):
    for p in files(w):
        assert remote_store.push_path(p)


def test_authorized_objects_are_confirmed_remotely_by_bytes_and_the_abstained_remote_object_is_untouched(lworld, lremote):
    _make_v3(lworld, lworld.a, lworld.b)
    _publish_all(lworld)
    spec = {"Bcme": [NONLB]}
    b_remote_key = next(k for k in lremote.objects if "Bcme" in k)
    b_remote = lremote.objects[b_remote_key]
    out = _apply(lworld, Engine(spec), ack=_digest_for(lworld, spec), waves=[["Acme", "Bcme"]], allow_local_only=False)
    assert out["outcome"] == bf.APPLIED_WITH_ABSTENTIONS and out["counts"]["regenerated_remote"] == 1 and out["counts"]["pending_remote"] == 0
    assert _rec(out, "Acme")["remote_state"] == svc.REMOTE_CONFIRMED and svc.verify_remote_durability("Acme", _env(_path(lworld, "Acme"))["terminal_am"]) == svc.REMOTE_CONFIRMED
    assert lremote.objects[b_remote_key] == b_remote and out["restart"] == bf.RESTART_SAFE and out["complete"] is False                # reinicio seguro, pero NO completa
    assert out["completion"] == bf.INCOMPLETE_PENDING_ABSTENTIONS and out["outcome"] != bf.APPLIED                                                      # con todo confirmado, lo unico pendiente son las abstenciones


def test_a_remote_write_that_is_not_confirmed_stops_the_waves_and_a_rerun_recovers(lworld, lremote):
    _make_v3(lworld, lworld.a, lworld.b)
    _publish_all(lworld)
    spec = {"Bcme": [NONLB]}
    ack = _digest_for(lworld, spec)
    lremote.fail_uploads = True
    out = _apply(lworld, Engine(spec), ack=ack, waves=[["Acme"], ["Bcme"]], allow_local_only=False)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and out["counts"]["pending_remote"] == 1 and out["restart"] == bf.RESTART_BLOCKED and out["restart_safe"] is False
    assert _rec(out, "Acme")["durable"] == svc.DURABLE_LOCAL and out["secured_apply"]["not_attempted_waves"] == [["Bcme"]] and out["completion"] == bf.INCOMPLETE
    lremote.fail_uploads = False
    again = _apply(lworld, Engine(spec), ack=_digest_for(lworld, spec), allow_local_only=False)
    assert again["outcome"] == bf.RECOVERY_ONLY and _rec(again, "Acme")["remote_state"] == svc.REMOTE_CONFIRMED and again["restart"] == bf.RESTART_SAFE
    assert again["completion"] == bf.INCOMPLETE_PENDING_ABSTENTIONS and again["complete"] is False


def test_a_put_that_reports_success_but_stores_other_bytes_is_not_confirmed_in_a_mixed_run(lworld, lremote, monkeypatch):
    _make_v3(lworld, lworld.a, lworld.b)
    _publish_all(lworld)
    spec = {"Bcme": [NONLB]}

    def lying_upload(filename, bucket, key):
        lremote.objects[key] = Path(filename).read_bytes()[:-1]
    monkeypatch.setattr(lremote, "upload_file", lying_upload)
    out = _apply(lworld, Engine(spec), ack=_digest_for(lworld, spec), waves=[["Acme", "Bcme"]], allow_local_only=False)
    assert out["outcome"] == bf.STOPPED_AT_WAVE and _rec(out, "Acme")["durable"] == svc.DURABLE_LOCAL and _rec(out, "Acme")["remote_state"] == svc.REMOTE_PENDING and out["restart"] == bf.RESTART_BLOCKED


def test_a_failed_durable_backup_publication_blocks_every_write(lworld, lremote):
    _make_v3(lworld, lworld.a, lworld.b)
    _publish_all(lworld)
    spec = {"Bcme": [NONLB]}
    snap, remote_before = _snapshot(lworld), dict(lremote.objects)
    lremote.fail_puts = True                                                                                        # el respaldo DURABLE no se puede sellar
    out = _apply(lworld, Engine(spec), ack=_digest_for(lworld, spec), waves=[["Acme", "Bcme"]], allow_local_only=False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["writes"] == 0 and _snapshot(lworld) == snap and lremote.objects == remote_before


# ───────────────────────────── restauracion ─────────────────────────────
def test_restore_returns_the_authorized_object_to_its_exact_bytes_and_never_touches_the_abstained_one(lworld):
    _make_v3(lworld, lworld.a, lworld.b)
    before = _snapshot(lworld)
    spec = {"Bcme": [NONLB]}
    out = _apply(lworld, Engine(spec), ack=_digest_for(lworld, spec), waves=[["Acme", "Bcme"]])
    assert out["outcome"] == bf.APPLIED_WITH_ABSTENTIONS and _snapshot(lworld) != before
    bk = Path(out["secured_apply"]["backup"]["dir"])
    rep = bf.restore_from_backup(bk, apply=False)
    assert rep["outcome"] == "DONE" and rep["counts"]["WOULD_RESTORE"] == 1 and len(rep["results"]) == 1               # solo el objeto autorizado esta en el respaldo
    seal = gb.load_manifest(bk)["manifest_sha256"]
    done = bf.restore_from_backup(bk, apply=True, confirm=seal[:12], authorizer=lambda m, p: ma.Authorization(True, "AUTHORIZED", approver="approver", operator="operator"))
    assert done["outcome"] == "DONE" and _snapshot(lworld) == before                                                  # todo, byte a byte, como al inicio
    again = bf.restore_from_backup(bk, apply=True, confirm=seal[:12], authorizer=lambda m, p: ma.Authorization(True, "AUTHORIZED", approver="approver", operator="operator"))
    assert again["results"][0]["status"] == "ALREADY_RESTORED"
    restart()
    assert svc.status_for_entry(lworld.a).pending_migration == MIG and svc.status_for_entry(lworld.b).pending_migration == MIG


# ───────────────────────────── CLI ─────────────────────────────
def _cli(monkeypatch, argv, report):
    from tools.catalog import catalog_service as cs
    from webapp.services import catalog_reads
    seen = {}
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, []))
    monkeypatch.setattr(bf, "secured_migrate", lambda es, name, **k: seen.update(k) or report)
    return bf.main(argv), seen


@pytest.mark.parametrize("outcome,expected", [(bf.ABSTENTIONS_CHANGED, bf.EXIT_DENIED), (bf.APPLIED_WITH_ABSTENTIONS, 3), (bf.APPLIED, 0), (bf.STOPPED_AT_WAVE, bf.EXIT_WAVE_STOPPED)])
def test_cli_passes_the_acknowledged_digest_and_maps_every_outcome_to_a_distinct_exit_code(monkeypatch, outcome, expected):
    report = {"outcome": outcome, "counts": {"failed": 0, "abstained": 1 if outcome == bf.APPLIED_WITH_ABSTENTIONS else 0}, "environment_warnings": []}
    code, seen = _cli(monkeypatch, ["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", MIG, "--accept-known-abstentions", "ab" * 32], report)
    assert code == expected and seen["known_abstentions"] == "ab" * 32


def test_cli_without_the_flag_keeps_the_original_behavior_and_the_flag_needs_a_secured_apply(monkeypatch, capsys):
    report = {"outcome": bf.APPLIED, "counts": {"failed": 0, "abstained": 0}, "environment_warnings": []}
    code, seen = _cli(monkeypatch, ["--regenerate-stale-evidence", MIG, "--apply", "--confirm-migration", MIG], report)
    assert code == 0 and seen["known_abstentions"] is None
    for argv in (["--regenerate-stale-evidence", MIG, "--dry-run", "--accept-known-abstentions", "x"], ["--accept-known-abstentions", "x", "--dry-run"]):
        with pytest.raises(SystemExit) as e:
            bf.main(argv)
        assert e.value.code == 2
