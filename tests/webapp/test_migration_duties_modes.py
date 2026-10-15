"""Modos de funciones de la autorizacion de migraciones: SEPARATED_DUTIES (por defecto, sin cambios) y OWNER_MANAGED (explicito, opt-in, una sola identidad acreditada por firma).
Cubre: ambos modos, todas las denegaciones, regresion del modo por defecto, registro auditable (reporte, diario AUTHORIZED, aprobacion archivada en el respaldo durable), CLI (issue/attest) y
fail-closed ante configuracion ausente o inconsistente. Spaces falso; nunca produccion."""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import MIG, _make_v3, _snapshot, kworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import _plan, _publish_all, _restore_approval, _secured, remote  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import durable_backup as dbk
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
OWN = Ed25519PrivateKey.generate()                      # clave de la identidad "owner"
ALICE = Ed25519PrivateKey.generate()                    # aprobador de un esquema de funciones separadas
KEYS = {"owner": OWN.public_key(), "alice": ALICE.public_key()}
PLAN = {"digest": "d" * 64, "clients": ["Kensing"]}
IDS = frozenset({"owner"})


def _owner_doc(*, attest=True, approver="owner", operator="owner", key=OWN, mode=ma.OWNER_MANAGED, att_key=OWN, att_identity="owner", att_issued=None, att_ttl=timedelta(minutes=30), plan=PLAN, **over) -> dict:
    kw = dict(migration=MIG, plan_digest=plan["digest"], clients=plan["clients"], approver=approver, operator=operator, private_key=key, issued_at=NOW - timedelta(minutes=5), ttl=timedelta(hours=2))
    kw.update(over)
    try:
        doc = ma.issue_approval(duties_mode=mode, **kw)
    except ValueError:                                   # casos que issue_approval ya rechaza se arman a mano para probar el verificador
        doc = {"schema": ma.APPROVAL_SCHEMA, "migration": MIG, "plan_digest": plan["digest"], "scope": {"clients": plan["clients"]}, "approver": approver, "authorized_operator": operator,
               "issued_at": ma._iso(NOW - timedelta(minutes=5)), "expires_at": ma._iso(NOW + timedelta(hours=2)), "duties_mode": mode}
        doc["signature"] = base64.b64encode(key.sign(ma.canonical(ma.signed_payload(doc)))).decode()
    if attest:
        doc = ma.attest_operator(approval=doc, identity=att_identity, private_key=att_key, issued_at=att_issued or NOW - timedelta(minutes=1), ttl=att_ttl)
    return doc


def _v(doc, *, operator="owner", ids=IDS, keys=KEYS, plan=PLAN, now=NOW):
    return ma.verify_approval(doc, migration=MIG, plan_digest=plan["digest"], plan_clients=plan["clients"], operator=operator, now=now, approver_keys=keys, owner_managed_ids=ids)


# ───────────────────────────── SEPARATED_DUTIES: por defecto y sin cambios ─────────────────────────────
def test_separated_duties_is_the_default_and_the_document_is_unchanged():
    doc = ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver="alice", operator="bob", private_key=ALICE, issued_at=NOW - timedelta(minutes=5))
    assert "duties_mode" not in doc and "operator_attestation" not in doc                                  # documento identico al de siempre (firmas previas siguen valiendo)
    a = _v(doc, operator="bob")
    assert a.granted and a.duties_mode == ma.SEPARATED_DUTIES and a.duties_collapsed is False and a.operator_assurance == ma.ASSURANCE_DESIGNATED and a.attestation is None


def test_same_identity_is_still_denied_in_the_default_mode_even_with_every_owner_setting_present():
    doc = ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver="owner", operator="owner", private_key=OWN, issued_at=NOW - timedelta(minutes=5))
    assert _v(doc).reason == ma.SAME_PERSON and not _v(doc).granted                                          # sin duties_mode OWNER_MANAGED no hay coincidencia posible
    sep = ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver="owner", operator="owner", private_key=OWN, issued_at=NOW - timedelta(minutes=5),
                            duties_mode=ma.SEPARATED_DUTIES)
    assert _v(ma.attest_operator(approval=sep, identity="owner", private_key=OWN, issued_at=NOW - timedelta(minutes=1)), ids=frozenset({"owner"})).reason == ma.SAME_PERSON   # la acreditacion no lo salva


def test_a_separated_approval_ignores_an_attestation_and_stays_separated():
    doc = ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver="alice", operator="bob", private_key=ALICE, issued_at=NOW - timedelta(minutes=5))
    doc = ma.attest_operator(approval=doc, identity="alice", private_key=ALICE, issued_at=NOW - timedelta(minutes=1))
    a = _v(doc, operator="bob")
    assert a.granted and a.duties_mode == ma.SEPARATED_DUTIES and a.operator_assurance == ma.ASSURANCE_DESIGNATED and a.attestation is None


# ───────────────────────────── OWNER_MANAGED: camino permitido ─────────────────────────────
def test_owner_managed_is_granted_with_formal_opt_in_single_identity_and_a_fresh_attestation():
    a = _v(_owner_doc())
    assert a.granted and a.reason == "AUTHORIZED" and a.duties_mode == ma.OWNER_MANAGED and a.duties_collapsed is True and a.operator_assurance == ma.ASSURANCE_ATTESTED
    assert a.approver == a.operator == "owner" and a.attestation["identity"] == "owner" and a.attestation["signature_sha256"] and a.document["duties_mode"] == ma.OWNER_MANAGED
    d = a.as_dict()
    assert d["duties_mode"] == ma.OWNER_MANAGED and d["duties_collapsed"] is True and d["operator_assurance"] == ma.ASSURANCE_ATTESTED and set(d["attestation"]) == {"identity", "issued_at", "expires_at", "signature_sha256"}


def test_issuing_owner_managed_requires_one_identity_no_fictitious_second_account():
    with pytest.raises(ValueError):
        ma.issue_approval(migration=MIG, plan_digest="d" * 64, clients=["K"], approver="owner", operator="owner2", private_key=OWN, duties_mode=ma.OWNER_MANAGED)
    with pytest.raises(ValueError):
        ma.issue_approval(migration=MIG, plan_digest="d" * 64, clients=["K"], approver="owner", operator="owner", private_key=OWN, duties_mode="OTRO")


# ───────────────────────────── OWNER_MANAGED: denegaciones ─────────────────────────────
@pytest.mark.parametrize("ids", [None, frozenset(), frozenset({"otro"})])
def test_owner_managed_needs_the_servers_formal_authorization_of_that_identity(ids, monkeypatch):
    monkeypatch.delenv(ma.ENV_OWNER_MANAGED_IDS, raising=False)
    a = _v(_owner_doc(), ids=ids)
    assert not a.granted and a.reason == (ma.OWNER_NOT_ENABLED if not ids else ma.OWNER_ID_NOT_AUTHORIZED)


def test_app_owner_and_environment_roles_do_not_enable_anything(monkeypatch):
    for k in ("APP_OWNER", "APP_OWNERS", "OWNER", "DANWOR_APP_OWNER"):
        monkeypatch.setenv(k, "owner")
    monkeypatch.delenv(ma.ENV_OWNER_MANAGED_IDS, raising=False)
    assert ma.verify_approval(_owner_doc(), migration=MIG, plan_digest=PLAN["digest"], plan_clients=PLAN["clients"], operator="owner", now=NOW, approver_keys=KEYS).reason == ma.OWNER_NOT_ENABLED
    assert not ma.authorizer_from_file(None, "owner", approver_keys=KEYS)(MIG, PLAN).granted


@pytest.mark.parametrize("raw, expected", [("", frozenset()), ("   ", frozenset()), ("{no es json", frozenset()), ('{"owner": 1}', frozenset()), ('["owner", 3]', frozenset()), ('[""]', frozenset()),
                                           ('"owner"', frozenset()), ('["owner"]', frozenset({"owner"})), ('["owner","ana"]', frozenset({"owner", "ana"}))])
def test_the_owner_managed_ids_config_is_strict_and_fails_closed(raw, expected, monkeypatch):
    assert ma.load_owner_managed_ids(raw) == expected
    monkeypatch.setenv(ma.ENV_OWNER_MANAGED_IDS, raw)
    assert ma.load_owner_managed_ids() == expected
    monkeypatch.delenv(ma.ENV_OWNER_MANAGED_IDS)
    assert ma.load_owner_managed_ids() == frozenset()


def test_the_env_config_is_what_the_default_verifier_reads(monkeypatch):
    doc = _owner_doc()
    kw = dict(migration=MIG, plan_digest=PLAN["digest"], plan_clients=PLAN["clients"], operator="owner", now=NOW, approver_keys=KEYS)
    monkeypatch.delenv(ma.ENV_OWNER_MANAGED_IDS, raising=False)
    assert ma.verify_approval(doc, **kw).reason == ma.OWNER_NOT_ENABLED
    monkeypatch.setenv(ma.ENV_OWNER_MANAGED_IDS, '["owner"]')
    assert ma.verify_approval(doc, **kw).granted


@pytest.mark.parametrize("make, operator, expected", [
    (lambda: _owner_doc(approver="owner", operator="owner2"), "owner2", ma.OWNER_SINGLE_IDENTITY),                       # dos identidades nominales para la misma persona
    (lambda: _owner_doc(), "owner2", ma.OPERATOR_MISMATCH),                                                               # el operador declarado no es el autorizado
    (lambda: _owner_doc(), "", ma.NO_OPERATOR),
    (lambda: _owner_doc(mode="OTRO_MODO"), "owner", ma.BAD_DUTIES_MODE),
    (lambda: _owner_doc(attest=False), "owner", ma.ATT_MISSING),                                                          # sin acreditacion no hay OWNER_MANAGED
    (lambda: _owner_doc(att_issued=NOW - timedelta(hours=3)), "owner", ma.ATT_EXPIRED),
    (lambda: _owner_doc(att_issued=NOW + timedelta(hours=1)), "owner", ma.ATT_FUTURE),
    (lambda: _owner_doc(att_key=ALICE), "owner", ma.ATT_INVALID),                                                         # firmada por otra clave
    (lambda: _owner_doc(att_identity="alice", att_key=ALICE), "owner", ma.ATT_IDENTITY),                                  # acredita a otra identidad
    (lambda: _owner_doc(approver="owner", key=ALICE), "owner", ma.BAD_SIGNATURE),                                         # la propia aprobacion mal firmada
    (lambda: _owner_doc(approver="mallory", operator="mallory"), "mallory", ma.UNKNOWN_APPROVER),
])
def test_owner_managed_denials_have_stable_reasons(make, operator, expected):
    a = _v(make(), operator=operator, ids=frozenset({"owner", "mallory", "owner2"}))
    assert not a.granted and a.reason == expected


def test_the_attestation_ttl_is_capped_and_cannot_be_issued_beyond_it():
    with pytest.raises(ValueError):
        ma.attest_operator(approval=_owner_doc(attest=False), identity="owner", private_key=OWN, ttl=ma.MAX_ATTESTATION_TTL + timedelta(seconds=1))
    doc = _owner_doc(attest=False)
    att = {"schema": ma.ATTESTATION_SCHEMA, "identity": "owner", "migration": MIG, "plan_digest": PLAN["digest"], "purpose": "execute",
           "approval_signature_sha256": hashlib.sha256(doc["signature"].encode()).hexdigest(), "issued_at": ma._iso(NOW - timedelta(minutes=1)), "expires_at": ma._iso(NOW + timedelta(hours=3))}
    att["signature"] = base64.b64encode(OWN.sign(ma.canonical(att))).decode()
    assert _v({**doc, "operator_attestation": att}).reason == ma.ATT_TTL


def test_an_attestation_cannot_be_reused_for_another_approval_or_plan():
    first = _owner_doc()
    other_plan = {"digest": "e" * 64, "clients": ["Kensing"]}
    second = _owner_doc(plan=other_plan, attest=False)
    stolen = {**second, "operator_attestation": first["operator_attestation"]}                                           # acreditacion de OTRA aprobacion/plan
    assert _v(stolen, plan=other_plan).reason == ma.ATT_OTHER_APPROVAL
    same_plan_new_approval = _owner_doc(attest=False, issued_at=NOW - timedelta(minutes=4))                              # mismo plan, aprobacion distinta (otra firma)
    assert _v({**same_plan_new_approval, "operator_attestation": first["operator_attestation"]}).reason == ma.ATT_OTHER_APPROVAL


def test_tampering_with_the_approval_after_signing_is_detected_but_the_attestation_field_is_not_part_of_the_signed_payload():
    doc = _owner_doc()
    assert _v({**doc, "plan_digest": "f" * 64}).reason == ma.BAD_SIGNATURE
    assert _v({**doc, "scope": {"clients": ["Otro"]}}).reason == ma.BAD_SIGNATURE
    assert _v({**doc, "duties_mode": ma.SEPARATED_DUTIES}).reason == ma.BAD_SIGNATURE                                    # no se puede degradar/cambiar el modo firmado
    assert ma.signed_payload(doc) == ma.signed_payload({**doc, "operator_attestation": {"x": 1}})
    forged = dict(doc["operator_attestation"], expires_at=ma._iso(NOW + timedelta(minutes=10)))
    assert _v({**doc, "operator_attestation": forged}).reason == ma.ATT_INVALID                                           # la acreditacion alterada no valida


@pytest.mark.parametrize("make, expected", [
    (lambda: _owner_doc(issued_at=NOW - timedelta(hours=5), ttl=timedelta(hours=1)), ma.EXPIRED),
    (lambda: _owner_doc(migration="evidence_confidence_v3"), ma.WRONG_MIGRATION),
    (lambda: _owner_doc(clients=["Otro"]), ma.SCOPE_NOT_COVERED),
    (lambda: _owner_doc(plan_digest="9" * 64), ma.PLAN_CHANGED),
])
def test_every_existing_control_still_applies_in_owner_managed(make, expected):
    a = _v(make())
    assert not a.granted and a.reason == expected


# ───────────────────────────── integracion: registro auditable y apply ─────────────────────────────
def _owner_file(w, plan, name="owner-approval.json", **kw):
    doc = ma.issue_approval(migration=MIG, plan_digest=plan["digest"], clients=plan["clients"], approver="owner", operator="owner", private_key=OWN, duties_mode=ma.OWNER_MANAGED,
                            issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    if kw.get("attest", True):
        doc = ma.attest_operator(approval=doc, identity="owner", private_key=OWN, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    f = w.tmp / name
    f.write_text(json.dumps(doc), encoding="utf-8")
    return f


def test_an_owner_managed_apply_is_authorized_audited_backed_up_and_reports_the_collapsed_duties(kworld, remote):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    plan = _plan(kworld)
    f = _owner_file(kworld, plan)
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, "owner", approver_keys=KEYS, owner_managed_ids=IDS), allow_local_only=False, run_id="run-own")
    assert out["outcome"] == bf.APPLIED and out["counts"]["failed"] == 0
    auth = out["secured_apply"]["authorization"]
    assert auth["duties_mode"] == ma.OWNER_MANAGED and auth["duties_collapsed"] is True and auth["operator_assurance"] == ma.ASSURANCE_ATTESTED and auth["approver"] == auth["operator"] == "owner"
    bk = kworld.tmp / "bk"
    first = [e for e in gb.read_journal(bk) if e["event"] == "AUTHORIZED"]
    assert len(first) == 1 and first[0]["duties_mode"] == ma.OWNER_MANAGED and first[0]["duties_collapsed"] is True and first[0]["plan_digest"] == plan["digest"]
    assert first[0]["approval_sha256"] == hashlib.sha256(ma.canonical(json.loads(f.read_text(encoding="utf-8")))).hexdigest() and first[0]["attestation"]["identity"] == "owner"
    order = [e["event"] for e in gb.read_journal(bk)]
    assert order.index("AUTHORIZED") < order.index("COMMITTED")                                                         # registrado ANTES de la primera escritura
    base = f"migration-backups/{MIG}/run-own/"
    archived = json.loads(remote.objects[base + "approval.json"])
    assert archived["duties_mode"] == ma.OWNER_MANAGED and archived["operator_attestation"]["identity"] == "owner"       # aprobacion completa archivada en el respaldo durable
    remote_events = [json.loads(v) for k, v in remote.objects.items() if k.startswith(base + "journal/")]
    assert any(e["event"] == "AUTHORIZED" and e["duties_mode"] == ma.OWNER_MANAGED for e in remote_events)             # y en el diario remoto


@pytest.mark.parametrize("case", ["no_opt_in", "no_attestation", "expired_attestation", "other_operator_label"])
def test_an_owner_managed_apply_without_every_condition_is_denied_with_zero_writes(kworld, remote, case):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    snap, before = _snapshot(kworld), dict(remote.objects)
    plan = _plan(kworld)
    f = _owner_file(kworld, plan, attest=(case != "no_attestation"))
    ids, operator = IDS, "owner"
    if case == "no_opt_in":
        ids = frozenset()
    elif case == "expired_attestation":
        doc = json.loads(f.read_text(encoding="utf-8"))
        f.write_text(json.dumps(ma.attest_operator(approval={k: v for k, v in doc.items() if k != "operator_attestation"}, identity="owner", private_key=OWN,
                                                   issued_at=datetime.now(timezone.utc) - timedelta(hours=3))), encoding="utf-8")
    elif case == "other_operator_label":
        operator = "owner-2"
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, operator, approver_keys=KEYS, owner_managed_ids=ids), allow_local_only=False)
    assert out["outcome"] == bf.DENIED and out["applied"] is False and out["writes"] == 0 and _snapshot(kworld) == snap and remote.objects == before and not (kworld.tmp / "bk").exists()


def test_the_remote_audit_event_failing_blocks_the_apply_before_any_write(kworld, remote, monkeypatch):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    snap = _snapshot(kworld)
    f = _owner_file(kworld, _plan(kworld))
    real = dbk.publish

    def publish_then_break(*a, **k):
        r = real(*a, **k)
        remote.fail_puts = True                                                                                         # el diario remoto no puede registrar la autorizacion
        return r
    monkeypatch.setattr(dbk, "publish", publish_then_break)
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, "owner", approver_keys=KEYS, owner_managed_ids=IDS), allow_local_only=False)
    assert out["outcome"] == bf.BACKUP_NOT_GUARANTEED and out["backup_error"] == "JOURNAL_REMOTE_FAILED" and _snapshot(kworld) == snap


def test_the_default_separated_apply_also_records_the_authorization_event(kworld, remote):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    plan = _plan(kworld, kworld.k)
    doc = ma.issue_approval(migration=MIG, plan_digest=plan["digest"], clients=plan["clients"], approver="alice", operator="bob", private_key=ALICE, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    f = kworld.tmp / "sep.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    out = _secured(kworld, kworld.k, authorizer=ma.authorizer_from_file(f, "bob", approver_keys=KEYS), allow_local_only=False)
    assert out["outcome"] == bf.APPLIED
    ev = next(e for e in gb.read_journal(kworld.tmp / "bk") if e["event"] == "AUTHORIZED")
    assert ev["duties_mode"] == ma.SEPARATED_DUTIES and ev["duties_collapsed"] is False and ev["operator_assurance"] == ma.ASSURANCE_DESIGNATED and ev["approver"] == "alice" and ev["operator"] == "bob"


def test_restore_accepts_an_owner_managed_approval_with_the_same_rules(kworld, remote):
    _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    before = {p.name: p.read_bytes() for p in files(kworld)}
    plan = _plan(kworld, kworld.k)
    f = _owner_file(kworld, plan)
    out = _secured(kworld, kworld.k, authorizer=ma.authorizer_from_file(f, "owner", approver_keys=KEYS, owner_managed_ids=IDS), allow_local_only=False)
    assert out["outcome"] == bf.APPLIED
    bk = kworld.tmp / "bk"
    m = gb.load_manifest(bk)
    rdoc = ma.issue_approval(migration=f"restore:{MIG}", plan_digest=m["manifest_sha256"], clients=sorted({o["client"] for o in m["objects"]}), approver="owner", operator="owner", private_key=OWN,
                             duties_mode=ma.OWNER_MANAGED, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    rdoc = ma.attest_operator(approval=rdoc, identity="owner", private_key=OWN, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    rf = kworld.tmp / "restore-owner.json"
    rf.write_text(json.dumps(rdoc), encoding="utf-8")
    res = bf.restore_from_backup(bk, apply=True, confirm=m["manifest_sha256"][:12], authorizer=ma.authorizer_from_file(rf, "owner", approver_keys=KEYS, owner_managed_ids=IDS))
    assert res["outcome"] == "DONE" and res["counts"]["RESTORED"] == 1 and {p.name: p.read_bytes() for p in files(kworld)} == before
    denied = bf.restore_from_backup(bk, apply=True, confirm=m["manifest_sha256"][:12], authorizer=ma.authorizer_from_file(rf, "owner", approver_keys=KEYS, owner_managed_ids=frozenset()))
    assert denied["outcome"] == "RESTORE_DENIED" and denied["authorization"]["reason"] == ma.OWNER_NOT_ENABLED


# ───────────────────────────── CLI del aprobador ─────────────────────────────
def test_the_cli_issues_owner_managed_approvals_and_attests_the_operator(tmp_path, capsys, monkeypatch):
    key = tmp_path / "k.key"
    assert ma.main(["keygen", "--out", str(key)]) == 0
    pub = capsys.readouterr().out.strip()
    keys = {"owner": ma.Ed25519PublicKey.from_public_bytes(base64.b64decode(pub))}
    out = tmp_path / "a.json"
    common = ["issue", "--key", str(key), "--migration", MIG, "--plan-digest", "d" * 64, "--client", "Kensing", "--approver", "owner", "--out", str(out)]
    with pytest.raises(SystemExit) as e:                                                                                  # dos identidades nominales: rechazado
        ma.main(common + ["--operator", "owner2", "--duties-mode", "OWNER_MANAGED"])
    assert e.value.code == 2 and not out.exists()
    assert ma.main(common + ["--operator", "owner", "--duties-mode", "OWNER_MANAGED"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["duties_mode"] == ma.OWNER_MANAGED and "operator_attestation" not in doc
    att = tmp_path / "att.json"
    assert ma.main(["attest", "--key", str(key), "--approval", str(out), "--identity", "owner", "--out", str(att)]) == 0
    full = json.loads(att.read_text(encoding="utf-8"))
    assert full["operator_attestation"]["identity"] == "owner" and {k: v for k, v in full.items() if k != "operator_attestation"} == doc
    a = ma.verify_approval(full, migration=MIG, plan_digest="d" * 64, plan_clients=["Kensing"], operator="owner", approver_keys=keys, owner_managed_ids=IDS)
    assert a.granted and a.operator_assurance == ma.ASSURANCE_ATTESTED
    with pytest.raises(SystemExit):                                                                                         # ttl de la acreditacion fuera de limite
        ma.main(["attest", "--key", str(key), "--approval", str(out), "--identity", "owner", "--ttl-minutes", "61", "--out", str(tmp_path / "x.json")])
    sep = tmp_path / "s.json"                                                                                               # el modo por defecto del CLI sigue siendo SEPARATED_DUTIES
    assert ma.main(["issue", "--key", str(key), "--migration", MIG, "--plan-digest", "d" * 64, "--client", "Kensing", "--approver", "owner", "--operator", "bob", "--out", str(sep)]) == 0
    assert "duties_mode" not in json.loads(sep.read_text(encoding="utf-8"))
