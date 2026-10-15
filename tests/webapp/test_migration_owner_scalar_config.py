"""OWNER_MANAGED con configuracion ESCALAR (``DANWOR_MIGRATION_OWNER_ID`` + ``DANWOR_MIGRATION_OWNER_PUBLIC_KEY``): el registro ``{id: clave}`` y la autorizacion formal se construyen internamente, sin JSON.
Cubre: carga/validacion de la configuracion, conflictos escalar-vs-JSON (se deniega TODO con causa explicita, nunca se elige una configuracion en silencio), que las variables escalares no autorizan nada por
si solas, SEPARATED_DUTIES sin cambios, registro auditable y el diagnostico ``config``. Claves EFIMERAS generadas en el test (no hay ninguna clave real en este archivo); Spaces falso."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import MIG, _make_v3, _snapshot, kworld  # noqa: F401
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import _plan, _publish_all, _secured, remote  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import governed_backup as gb
from webapp.services import migration_approval as ma

NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
OWN, ALICE, OTHER = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
PLAN = {"digest": "d" * 64, "clients": ["Kensing"]}
OWN_PUB, ALICE_PUB, OTHER_PUB = ma.public_key_b64(OWN), ma.public_key_b64(ALICE), ma.public_key_b64(OTHER)
SCALAR = {ma.ENV_OWNER_ID: "owner", ma.ENV_OWNER_PUBLIC_KEY: OWN_PUB}
ALL_ENV = (ma.ENV_OWNER_ID, ma.ENV_OWNER_PUBLIC_KEY, ma.ENV_APPROVER_KEYS, ma.ENV_OWNER_MANAGED_IDS)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ALL_ENV:
        monkeypatch.delenv(k, raising=False)


def _env(monkeypatch, **vals):
    for k, v in vals.items():
        monkeypatch.setenv(k, v)


def _owner_doc(*, attest=True, key=OWN, att_key=OWN, approver="owner", plan=PLAN, **over) -> dict:
    kw = dict(migration=MIG, plan_digest=plan["digest"], clients=plan["clients"], approver=approver, operator=approver, private_key=key, issued_at=NOW - timedelta(minutes=5), ttl=timedelta(hours=2))
    kw.update(over)
    doc = ma.issue_approval(duties_mode=ma.OWNER_MANAGED, **kw)
    return ma.attest_operator(approval=doc, identity=approver, private_key=att_key, issued_at=NOW - timedelta(minutes=1)) if attest else doc


def _sep_doc(approver="alice", operator="bob", key=ALICE) -> dict:
    return ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver=approver, operator=operator, private_key=key, issued_at=NOW - timedelta(minutes=5))


def _v(doc, operator="owner"):
    """Verificacion por el camino de PRODUCCION: sin registro explicito, solo el entorno."""
    return ma.verify_approval(doc, migration=MIG, plan_digest=PLAN["digest"], plan_clients=PLAN["clients"], operator=operator, now=NOW)


# ───────────────────────────── carga y validacion de la configuracion ─────────────────────────────
def test_without_scalar_variables_the_legacy_json_configuration_is_unchanged():
    c = ma.load_identity_config({ma.ENV_APPROVER_KEYS: json.dumps({"owner": OWN_PUB, "alice": ALICE_PUB}), ma.ENV_OWNER_MANAGED_IDS: '["owner"]'})
    assert c.source == ma.CFG_LEGACY and c.error is None and sorted(c.separated_keys) == ["alice", "owner"] and c.owner_ids == frozenset({"owner"}) and c.owner_keys == c.separated_keys
    empty = ma.load_identity_config({})
    assert empty.source == ma.CFG_LEGACY and not empty.separated_keys and not empty.owner_ids and empty.error is None


def test_valid_scalar_configuration_builds_the_owner_registry_and_formal_authorization_internally():
    c = ma.load_identity_config(SCALAR)
    assert c.source == ma.CFG_SCALAR and c.error is None and c.owner_id == "owner" and list(c.owner_keys) == ["owner"] and c.owner_ids == frozenset({"owner"})
    assert c.separated_keys == {} and c.key_fingerprint and len(c.key_fingerprint) == 16                       # SEPARATED_DUTIES no recibe nada de las escalares
    padded = ma.load_identity_config({ma.ENV_OWNER_ID: " owner\n", ma.ENV_OWNER_PUBLIC_KEY: "  " + OWN_PUB + "\n"})
    assert padded.error is None and padded.owner_id == "owner"                                                 # espacios/saltos de linea de pegar secretos


@pytest.mark.parametrize("env, code", [
    ({ma.ENV_OWNER_ID: "owner"}, ma.CFG_INCOMPLETE),
    ({ma.ENV_OWNER_PUBLIC_KEY: OWN_PUB}, ma.CFG_INCOMPLETE),
    ({ma.ENV_OWNER_ID: "   ", ma.ENV_OWNER_PUBLIC_KEY: OWN_PUB}, ma.CFG_INCOMPLETE),
    ({ma.ENV_OWNER_ID: "own er", ma.ENV_OWNER_PUBLIC_KEY: OWN_PUB}, ma.CFG_BAD_ID),
    ({ma.ENV_OWNER_ID: "owner", ma.ENV_OWNER_PUBLIC_KEY: "no-es-base64!!"}, ma.CFG_BAD_KEY),
    ({ma.ENV_OWNER_ID: "owner", ma.ENV_OWNER_PUBLIC_KEY: base64.b64encode(b"corta").decode()}, ma.CFG_BAD_KEY),       # no son 32 bytes
    ({ma.ENV_OWNER_ID: "owner", ma.ENV_OWNER_PUBLIC_KEY: base64.b64encode(b"x" * 33).decode()}, ma.CFG_BAD_KEY),
])
def test_partial_or_invalid_scalar_configuration_is_an_explicit_error(env, code):
    c = ma.load_identity_config(env)
    assert c.error == code and not c.owner_keys and not c.separated_keys and not c.owner_ids


# ───────────────────────────── conflictos escalar vs JSON ─────────────────────────────
@pytest.mark.parametrize("extra, code", [
    ({ma.ENV_APPROVER_KEYS: json.dumps({"owner": ALICE_PUB})}, ma.CFG_KEY_CONFLICT),                         # misma identidad, otra clave
    ({ma.ENV_APPROVER_KEYS: json.dumps({"bob": OWN_PUB})}, ma.CFG_KEY_SHARED),                               # la misma clave bajo otra identidad
    ({ma.ENV_OWNER_MANAGED_IDS: '["alice"]'}, ma.CFG_IDS_CONFLICT),                                          # ids de OWNER_MANAGED que contradicen
    ({ma.ENV_OWNER_MANAGED_IDS: '["owner","alice"]'}, ma.CFG_IDS_CONFLICT),                                  # superconjunto ambiguo
    ({ma.ENV_APPROVER_KEYS: "{no es json"}, ma.CFG_LEGACY_INVALID),                                          # JSON ilegible: no se descarta en silencio
    ({ma.ENV_APPROVER_KEYS: json.dumps({"alice": "!!"})}, ma.CFG_LEGACY_INVALID),
    ({ma.ENV_OWNER_MANAGED_IDS: "{no es json"}, ma.CFG_LEGACY_INVALID),
    ({ma.ENV_OWNER_MANAGED_IDS: '[3]'}, ma.CFG_LEGACY_INVALID),
])
def test_contradictory_scalar_and_json_configuration_is_denied_explicitly(extra, code):
    c = ma.load_identity_config({**SCALAR, **extra})
    assert c.error == code and not c.owner_keys and not c.separated_keys


@pytest.mark.parametrize("extra", [
    {}, {ma.ENV_APPROVER_KEYS: json.dumps({"alice": ALICE_PUB})},                                             # JSON con OTROS aprobadores: coexistencia legitima
    {ma.ENV_APPROVER_KEYS: json.dumps({"owner": OWN_PUB})},                                                   # mismo id y misma clave: redundante, coherente
    {ma.ENV_APPROVER_KEYS: json.dumps({"owner": OWN_PUB, "alice": ALICE_PUB}), ma.ENV_OWNER_MANAGED_IDS: '["owner"]'},
])
def test_consistent_coexistence_of_scalar_and_json_is_accepted(extra):
    c = ma.load_identity_config({**SCALAR, **extra})
    assert c.error is None and c.source == ma.CFG_SCALAR and c.owner_ids == frozenset({"owner"})


def test_a_conflict_denies_every_authorization_in_every_mode_with_the_explicit_cause(monkeypatch):
    _env(monkeypatch, **SCALAR, **{ma.ENV_APPROVER_KEYS: json.dumps({"owner": ALICE_PUB, "alice": ALICE_PUB})})
    for doc, op in ((_owner_doc(), "owner"), (_sep_doc(), "bob")):
        a = _v(doc, op)
        assert not a.granted and a.reason.startswith("OWNER_CONFIG_CONFLICT") and a.identity_config == ma.CFG_SCALAR
    monkeypatch.setenv(ma.ENV_OWNER_MANAGED_IDS, '["alice"]')
    monkeypatch.setenv(ma.ENV_APPROVER_KEYS, json.dumps({"alice": ALICE_PUB}))
    assert _v(_owner_doc()).reason == ma.CFG_IDS_CONFLICT and _v(_sep_doc(), "bob").reason == ma.CFG_IDS_CONFLICT


# ───────────────────────────── OWNER_MANAGED con escalares: garantias intactas ─────────────────────────────
def test_owner_managed_is_granted_with_only_the_two_scalar_variables(monkeypatch):
    _env(monkeypatch, **SCALAR)
    a = _v(_owner_doc())
    assert a.granted and a.duties_mode == ma.OWNER_MANAGED and a.duties_collapsed and a.operator_assurance == ma.ASSURANCE_ATTESTED and a.identity_config == ma.CFG_SCALAR
    assert a.approver == a.operator == "owner" and a.as_dict()["identity_config"] == ma.CFG_SCALAR


def test_the_scalar_variables_do_not_authorize_anything_by_themselves(monkeypatch):
    _env(monkeypatch, **SCALAR)
    assert ma.authorizer_from_file(None, "owner")(MIG, PLAN).reason == ma.NO_APPROVAL                           # sin aprobacion: nada
    assert _v(None).reason == ma.NO_APPROVAL
    assert _v(_owner_doc(attest=False)).reason == ma.ATT_MISSING                                               # firma valida pero sin acreditacion
    assert _v(_owner_doc(key=ALICE, att_key=ALICE)).reason == ma.BAD_SIGNATURE                                  # el id coincide pero la firma es de otra clave
    assert _v(_owner_doc(approver="mallory", key=OTHER, att_key=OTHER)).reason == ma.UNKNOWN_APPROVER           # otra identidad no registrada
    assert _v(_owner_doc(), operator="mallory").reason == ma.OPERATOR_MISMATCH                                  # ser APP_OWNER/tener la consola no basta
    assert _v(_owner_doc(att_key=ALICE)).reason == ma.ATT_INVALID                                               # acreditacion de otra clave
    assert _v({**_owner_doc(), "plan_digest": "f" * 64}).reason == ma.BAD_SIGNATURE


@pytest.mark.parametrize("make, expected", [
    (lambda: _owner_doc(issued_at=NOW - timedelta(hours=5), ttl=timedelta(hours=1)), ma.EXPIRED),
    (lambda: _owner_doc(migration="evidence_confidence_v3"), ma.WRONG_MIGRATION),
    (lambda: _owner_doc(clients=["Otro"]), ma.SCOPE_NOT_COVERED),
    (lambda: _owner_doc(plan_digest="9" * 64), ma.PLAN_CHANGED),
])
def test_every_existing_control_still_applies_with_scalar_configuration(make, expected, monkeypatch):
    _env(monkeypatch, **SCALAR)
    assert _v(make()).reason == expected


def test_owner_managed_with_scalar_uses_only_the_scalar_registry_not_the_json_one(monkeypatch):
    _env(monkeypatch, **SCALAR, **{ma.ENV_APPROVER_KEYS: json.dumps({"alice": ALICE_PUB})})
    assert _v(_owner_doc()).granted                                                                            # el owner escalar autoriza OWNER_MANAGED aunque el JSON liste a otros
    alice_owner = ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver="alice", operator="alice", private_key=ALICE, duties_mode=ma.OWNER_MANAGED,
                                    issued_at=NOW - timedelta(minutes=5))
    alice_owner = ma.attest_operator(approval=alice_owner, identity="alice", private_key=ALICE, issued_at=NOW - timedelta(minutes=1))
    assert _v(alice_owner, "alice").reason == ma.UNKNOWN_APPROVER                                              # alice NO esta autorizada para OWNER_MANAGED (solo el id escalar)


def test_legacy_json_owner_managed_still_works_when_no_scalar_variables_exist(monkeypatch):
    _env(monkeypatch, **{ma.ENV_APPROVER_KEYS: json.dumps({"owner": OWN_PUB}), ma.ENV_OWNER_MANAGED_IDS: '["owner"]'})
    a = _v(_owner_doc())
    assert a.granted and a.identity_config == ma.CFG_LEGACY
    monkeypatch.delenv(ma.ENV_OWNER_MANAGED_IDS)
    assert _v(_owner_doc()).reason == ma.OWNER_NOT_ENABLED                                                      # sin escalares el opt-in JSON sigue siendo obligatorio


# ───────────────────────────── SEPARATED_DUTIES sin cambios ─────────────────────────────
def test_separated_duties_is_unchanged_by_the_presence_of_scalar_variables(monkeypatch):
    _env(monkeypatch, **SCALAR, **{ma.ENV_APPROVER_KEYS: json.dumps({"alice": ALICE_PUB})})
    a = _v(_sep_doc(), "bob")
    assert a.granted and a.duties_mode == ma.SEPARATED_DUTIES and a.operator_assurance == ma.ASSURANCE_DESIGNATED and a.identity_config == ma.CFG_LEGACY and a.duties_collapsed is False
    # el owner escalar NO es aprobador de SEPARATED_DUTIES (su configuracion sigue siendo SOLO el JSON de aprobadores)
    owner_sep = ma.issue_approval(migration=MIG, plan_digest=PLAN["digest"], clients=PLAN["clients"], approver="owner", operator="bob", private_key=OWN, issued_at=NOW - timedelta(minutes=5))
    assert _v(owner_sep, "bob").reason == ma.UNKNOWN_APPROVER
    assert _v(_sep_doc(approver="alice", operator="alice"), "alice").reason == ma.SAME_PERSON                  # aprobador == operador sigue denegado
    monkeypatch.delenv(ma.ENV_APPROVER_KEYS)
    assert _v(_sep_doc(), "bob").reason == ma.NO_APPROVERS                                                      # sin JSON, SEPARATED_DUTIES no tiene aprobadores (como siempre)


def test_the_default_deployment_without_any_new_variable_behaves_exactly_as_before(monkeypatch):
    _env(monkeypatch, **{ma.ENV_APPROVER_KEYS: json.dumps({"alice": ALICE_PUB})})
    a = _v(_sep_doc(), "bob")
    assert a.granted and a.identity_config == ma.CFG_LEGACY and a.duties_mode == ma.SEPARATED_DUTIES
    assert _v(_owner_doc(approver="alice", key=ALICE, att_key=ALICE), "alice").reason == ma.OWNER_NOT_ENABLED   # OWNER_MANAGED sigue siendo opt-in explicito


# ───────────────────────────── integracion: apply y registro auditable ─────────────────────────────
def _write_owner_file(w, plan):
    doc = ma.issue_approval(migration=MIG, plan_digest=plan["digest"], clients=plan["clients"], approver="owner", operator="owner", private_key=OWN, duties_mode=ma.OWNER_MANAGED,
                            issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    doc = ma.attest_operator(approval=doc, identity="owner", private_key=OWN, issued_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    f = w.tmp / "owner.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return f


def test_an_apply_authorized_with_only_scalar_configuration_records_the_config_source(kworld, remote, monkeypatch):
    _env(monkeypatch, **SCALAR)                                                                                 # SIN JSON de ningun tipo
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    f = _write_owner_file(kworld, _plan(kworld))
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, "owner"), allow_local_only=False, run_id="run-scalar")
    assert out["outcome"] == bf.APPLIED and out["counts"]["failed"] == 0
    auth = out["secured_apply"]["authorization"]
    assert auth["identity_config"] == ma.CFG_SCALAR and auth["duties_mode"] == ma.OWNER_MANAGED and auth["duties_collapsed"] is True
    ev = next(e for e in gb.read_journal(kworld.tmp / "bk") if e["event"] == "AUTHORIZED")
    assert ev["identity_config"] == ma.CFG_SCALAR and ev["duties_mode"] == ma.OWNER_MANAGED
    remote_ev = [json.loads(v) for k, v in remote.objects.items() if k.startswith(f"migration-backups/{MIG}/run-scalar/journal/")]
    assert any(e["event"] == "AUTHORIZED" and e["identity_config"] == ma.CFG_SCALAR for e in remote_ev)


@pytest.mark.parametrize("breaker", ["conflict", "incomplete", "bad_key", "scalar_only_no_approval"])
def test_a_broken_or_conflicting_configuration_blocks_the_apply_with_zero_writes(kworld, remote, monkeypatch, breaker):
    _make_v3(kworld, kworld.k, kworld.k2)
    _publish_all(kworld)
    snap, before = _snapshot(kworld), dict(remote.objects)
    f = _write_owner_file(kworld, _plan(kworld))
    if breaker == "conflict":
        _env(monkeypatch, **SCALAR, **{ma.ENV_APPROVER_KEYS: json.dumps({"owner": ALICE_PUB})})
    elif breaker == "incomplete":
        _env(monkeypatch, **{ma.ENV_OWNER_ID: "owner"})
    elif breaker == "bad_key":
        _env(monkeypatch, **{ma.ENV_OWNER_ID: "owner", ma.ENV_OWNER_PUBLIC_KEY: "xxxx"})
    else:
        _env(monkeypatch, **SCALAR)
        f = kworld.tmp / "missing.json"                                                                           # las variables escalares solas jamas autorizan un apply
    out = _secured(kworld, authorizer=ma.authorizer_from_file(f, "owner"), allow_local_only=False)
    assert out["outcome"] == bf.DENIED and out["applied"] is False and out["writes"] == 0 and _snapshot(kworld) == snap and remote.objects == before and not (kworld.tmp / "bk").exists()
    if breaker != "scalar_only_no_approval":
        assert out["authorization"]["reason"].startswith("OWNER_CONFIG_")


# ───────────────────────────── diagnostico sin secretos ─────────────────────────────
def test_the_config_command_shows_the_effective_configuration_without_key_material(monkeypatch, capsys):
    _env(monkeypatch, **SCALAR, **{ma.ENV_APPROVER_KEYS: json.dumps({"alice": ALICE_PUB})})
    assert ma.main(["config"]) == 0
    out = capsys.readouterr().out
    info = json.loads(out)
    assert info["source"] == ma.CFG_SCALAR and info["error"] is None and info["owner_id"] == "owner" and info["owner_managed_available"] is True and info["approver_ids_for_separated_duties"] == ["alice"]
    assert info["owner_key_fingerprint_sha256_16"] and OWN_PUB not in out and ALICE_PUB not in out                # ni siquiera la clave publica: solo una huella
    monkeypatch.setenv(ma.ENV_APPROVER_KEYS, json.dumps({"owner": ALICE_PUB}))
    assert ma.main(["config"]) == 2
    bad = json.loads(capsys.readouterr().out)
    assert bad["error"] == ma.CFG_KEY_CONFLICT and bad["owner_managed_available"] is False and bad["separated_duties_available"] is False


def test_the_config_command_reports_the_legacy_configuration(monkeypatch, capsys):
    _env(monkeypatch, **{ma.ENV_APPROVER_KEYS: json.dumps({"owner": OWN_PUB}), ma.ENV_OWNER_MANAGED_IDS: '["owner"]'})
    assert ma.main(["config"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["source"] == ma.CFG_LEGACY and info["owner_managed_ids"] == ["owner"] and info["owner_managed_available"] is True and info["owner_key_fingerprint_sha256_16"] is None
    for k in ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    assert ma.main(["config"]) == 0 and json.loads(capsys.readouterr().out)["owner_managed_available"] is False
