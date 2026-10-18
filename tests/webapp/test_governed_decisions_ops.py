"""Tests focalizados de la capa FUNCIONAL `governed_decisions_ops` (v4.B / B3).

Spaces FALSO en memoria (patrón de B2) + almacén de Assessments gobernados REAL en `tmp_path` (sobres firmados en disco) para
ejercitar la LECTURA ESTRICTA (`_iter_client_envelopes_strict`): distingue ausencia real, perfil válido sin match y perfil
existente ilegible/alterado (fail-closed). Cubre resolución per-CDS (Alt 1 cross-scan + consistencia), identidad
(remote_connection, remote_entity), ratificación/idempotencia A/conflicto, revocación (vigente/huérfana/repetida/idempotente),
re-ratificación, consulta técnica y la INDEPENDENCIA de Judgment. No conecta a Spaces real, no toca prod."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
from core import remote_store
from webapp.services import assessment_service as _assess
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_ops as ops


# ─────────────────────────── Spaces falso (almacén de decisiones, idéntico a B2) ───────────────────────────

class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeSpaces:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.corrupt_puts = False

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = b"CORRUPTO" if self.corrupt_puts else bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoSuchKey()
        return {"Body": SimpleNamespace(read=lambda k=Key: self.objects[k])}

    def get_paginator(self, _name):
        bucket = self

        class _P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in sorted(bucket.objects) if k.startswith(Prefix)]}]

        return _P()


# ─────────────────────────── almacén de Assessments gobernados REAL en disco ───────────────────────────

class GovStore:
    """Escribe sobres gobernados FIRMADOS (y variantes corruptas) bajo `<root>/v1/<slug>/`, igual que `assessment_service`."""

    def __init__(self, root) -> None:
        self.root = root

    def _client_dir(self, client):
        d = self.root / _assess.GOVERNED_LAYOUT_VERSION / (_assess._SAFE.sub("_", client).strip(".") or "_")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, *, client="Kensing", am="AM_X", leaves):
        profile = {"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}}
        env = {"envelope_version": _assess.ENVELOPE_VERSION, "client": client, "terminal_am": am,
               "profile_json": profile, "assessment_artifact_signature": _assess._artifact_signature(profile)}
        (self._client_dir(client) / f"{_assess._SAFE.sub('_', am).strip('.')}__{abs(hash(am)) % 10**8:08d}.json").write_text(
            json.dumps(env), encoding="utf-8")

    def write_raw(self, *, client="Kensing", name="corrupt.json", text="{ no es json válido"):
        (self._client_dir(client) / name).write_text(text, encoding="utf-8")

    def write_tampered(self, *, client="Kensing", am="AM_T", leaves):
        # sobre bien formado pero con firma de artefacto que NO corresponde al profile (alterado)
        profile = {"canonical_evidence": {"source_contract_classes": {"leaves": list(leaves)}}}
        env = {"envelope_version": _assess.ENVELOPE_VERSION, "client": client, "terminal_am": am,
               "profile_json": profile, "assessment_artifact_signature": "deadbeef" * 8}
        (self._client_dir(client) / f"{am}__tamper.json").write_text(json.dumps(env), encoding="utf-8")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Spaces falso (decisiones) + almacén de Assessments gobernados real en tmp_path."""
    bucket = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: bucket)
    gov_root = tmp_path / "governed"
    monkeypatch.setattr(_assess, "governed_dir", lambda: gov_root)
    return SimpleNamespace(bucket=bucket, gov=GovStore(gov_root), root=gov_root)


def _leaf(*, entity="ABAP_CDS.ZBILL", conn="S4H_PROD", cls="GOVERNED_SAP_CDS_CONTRACT",
          bases=("NAMESPACE",), kind="REMOTE_IMPORTED", leaf="ZBILL", entities=None):
    return {"leaf": leaf, "leaf_kind": kind, "contract_class": cls,
            "classification_basis": bases[0] if bases else None, "classification_bases": list(bases),
            "boundary_classes": [], "source_systems": ["S4H"],
            "remote_entities": list(entities) if entities is not None else [entity],
            "remote_connection": conn, "definition": "narrativa de contexto, NO entra al digest"}


IDENT = {"remote_connection": "S4H_PROD", "remote_entity": "ABAP_CDS.ZBILL"}
NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
LATER2 = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════ BLOQUE 1 — lectura estricta de evidencia ═══════════════════════════

def test_zero_profiles_is_real_absence(env):
    with pytest.raises(ops.ContractEvidenceInsufficient):
        ops.resolve_cds_contract("Kensing", IDENT)


def test_valid_profile_without_match_is_insufficient_not_error(env):
    env.gov.write(leaves=[_leaf(entity="ABAP_CDS.OTRO")])
    with pytest.raises(ops.ContractEvidenceInsufficient):          # ausencia de match ≠ corrupción
        ops.resolve_cds_contract("Kensing", IDENT)


def test_single_corrupt_profile_fails_closed(env):
    env.gov.write_raw(name="broken.json")
    with pytest.raises(ops.ContractEvidenceUnverifiable):          # existente pero ilegible → NO se trata como ausencia
        ops.resolve_cds_contract("Kensing", IDENT)


def test_two_valid_one_corrupt_fails_closed(env):
    env.gov.write(am="AM_A", leaves=[_leaf(leaf="N1")])
    env.gov.write(am="AM_B", leaves=[_leaf(leaf="N2")])
    env.gov.write_raw(name="broken.json")
    with pytest.raises(ops.ContractEvidenceUnverifiable):          # no se puede garantizar consistencia completa
        ops.resolve_cds_contract("Kensing", IDENT)


def test_unknown_structure_profile_fails_closed(env):
    env.gov.write_raw(name="weird.json", text=json.dumps({"hello": "world"}))   # JSON válido, no es un sobre
    with pytest.raises(ops.ContractEvidenceUnverifiable):
        ops.resolve_cds_contract("Kensing", IDENT)


def test_tampered_signature_fails_closed(env):
    env.gov.write_tampered(leaves=[_leaf()])                        # firma de artefacto no coincide
    with pytest.raises(ops.ContractEvidenceUnverifiable):
        ops.resolve_cds_contract("Kensing", IDENT)


def test_listing_error_fails_closed(env, monkeypatch):
    env.gov.write(leaves=[_leaf()])
    import pathlib
    orig = pathlib.Path.glob

    def boom(self, pat):
        if self.name == (_assess._SAFE.sub("_", "Kensing").strip(".")):
            raise OSError("fallo de listado simulado")
        return orig(self, pat)

    monkeypatch.setattr(pathlib.Path, "glob", boom)
    with pytest.raises(ops.ContractEvidenceUnverifiable):
        ops.resolve_cds_contract("Kensing", IDENT)


def test_contradictory_evidence_is_ambiguous(env):
    env.gov.write(am="AM_A", leaves=[_leaf(cls="GOVERNED_SAP_CDS_CONTRACT")])
    env.gov.write(am="AM_B", leaves=[_leaf(cls="EXTERNAL_PHYSICAL_OR_TECHNICAL")])
    with pytest.raises(ops.ContractEvidenceAmbiguous):
        ops.resolve_cds_contract("Kensing", IDENT)


def test_consistent_multi_consumer_resolves(env):
    env.gov.write(am="AM_A", leaves=[_leaf(leaf="N1")])
    env.gov.write(am="AM_B", leaves=[_leaf(leaf="N2")])            # mismo slice contractual, distinto contexto
    r = ops.resolve_cds_contract("Kensing", IDENT)
    assert sorted(r["consumers"]) == ["AM_A", "AM_B"]
    assert r["slice"]["contract_class"] == "GOVERNED_SAP_CDS_CONTRACT"
    assert len(r["digest"]) == 64


def test_context_difference_is_not_ambiguous(env):
    a = _leaf(leaf="N1"); b = _leaf(leaf="N2"); b["source_systems"] = ["OTHER"]
    env.gov.write(am="AM_A", leaves=[a])
    env.gov.write(am="AM_B", leaves=[b])
    r = ops.resolve_cds_contract("Kensing", IDENT)                 # difieren leaf/source_systems → NO contradicción
    assert sorted(r["consumers"]) == ["AM_A", "AM_B"]


def test_connection_disambiguates_same_entity(env):
    env.gov.write(am="AM_A", leaves=[_leaf(conn="S4H_PROD", cls="GOVERNED_SAP_CDS_CONTRACT")])
    env.gov.write(am="AM_B", leaves=[_leaf(conn="S4H_QAS", cls="EXTERNAL_PHYSICAL_OR_TECHNICAL")])
    r = ops.resolve_cds_contract("Kensing", {"remote_connection": "S4H_PROD", "remote_entity": "ABAP_CDS.ZBILL"})
    assert r["slice"]["contract_class"] == "GOVERNED_SAP_CDS_CONTRACT"
    assert r["consumers"] == ["AM_A"]


def test_non_remote_imported_leaf_is_not_subject(env):
    env.gov.write(leaves=[_leaf(kind="LOCAL_FLOW_PRODUCED")])
    with pytest.raises(ops.ContractEvidenceInsufficient):
        ops.resolve_cds_contract("Kensing", IDENT)


def test_other_client_profiles_are_ignored(env):
    env.gov.write(client="OtroCliente", leaves=[_leaf()])
    with pytest.raises(ops.ContractEvidenceInsufficient):          # el escaneo se acota al dir del cliente pedido
        ops.resolve_cds_contract("Kensing", IDENT)


def test_other_client_corruption_does_not_block(env):
    env.gov.write(leaves=[_leaf()])                                # Kensing OK
    env.gov.write_raw(client="OtroCliente", name="broken.json")   # corrupción de OTRO cliente
    r = ops.resolve_cds_contract("Kensing", IDENT)                 # no bloquea esta operación
    assert r["consumers"] == ["AM_X"]


def test_missing_remote_entity_is_insufficient(env):
    with pytest.raises(ops.ContractEvidenceInsufficient):
        ops.resolve_cds_contract("Kensing", {"remote_connection": "S4H_PROD", "remote_entity": ""})


# ─── equivalencia con el inventario real (no omitir perfiles; mismo criterio que iter_governed_envelopes) ───

def test_client_case_mismatch_still_resolves(env):
    # persistido con "Kensing" (dir "Kensing"); ratificación pedida con "kensing" → NO debe omitirse (FS case-sensitive)
    env.gov.write(client="Kensing", leaves=[_leaf()])
    r = ops.resolve_cds_contract("kensing", IDENT)
    assert r["slice"]["contract_class"] == "GOVERNED_SAP_CDS_CONTRACT"
    assert r["consumers"] == ["AM_X"]


def test_strict_reader_matches_real_loader_for_readable_profiles(env):
    # tres clientes, varios AMs: el set de (client, am) del lector estricto == el del loader real filtrado por client
    env.gov.write(client="Kensing", am="AM_A", leaves=[_leaf(leaf="N1")])
    env.gov.write(client="Kensing", am="AM_B", leaves=[_leaf(entity="ABAP_CDS.OTRO")])
    env.gov.write(client="Otro", am="AM_C", leaves=[_leaf()])
    strict = {(e["client"], e["terminal_am"]) for e in ops._iter_client_envelopes_strict("Kensing")}
    real = {(e["client"], e["terminal_am"]) for e, _p in _assess.iter_governed_envelopes()
            if str(e.get("client", "")).lower() == "kensing"}
    assert strict == real == {("Kensing", "AM_A"), ("Kensing", "AM_B")}


def test_slug_collision_keeps_clients_separate(env):
    # "Kensing" y "Kensing!" colisionan al slug "Kensing_"? No: _SAFE deja "Kensing" y "Kensing_". Validamos aislamiento real.
    env.gov.write(client="Kensing", am="AM_A", leaves=[_leaf()])
    env.gov.write(client="ACME.Corp", am="AM_D", leaves=[_leaf(entity="ABAP_CDS.ACME")])
    r = ops.resolve_cds_contract("Kensing", IDENT)
    assert r["consumers"] == ["AM_A"]                              # no mezcla con ACME.Corp


# ═══════════════════════════ BLOQUE 2 — idempotencia y consistencia del historial ═══════════════════════════

def _ratify(env, **over):
    kw = dict(client="Kensing", identity=IDENT, rationale="contrato gobernado", actor="owner@danwor.com",
              now=NOW, event_id="gd-1", request_id="req-1")
    kw.update(over)
    return ops.ratify(**kw)


def test_ratify_persists_and_confirms(env):
    env.gov.write(leaves=[_leaf()])
    out = _ratify(env)
    assert out["outcome"] == "RATIFIED"
    assert out["event"]["actor"] == "owner@danwor.com"
    assert out["event"]["evidence_ref"]["fields"] == list(gd.CDS_DIGEST_FIELDS)
    assert ops.status("Kensing", IDENT, LATER)["status"]["decision"] == gd.DECISION_RATIFIED


def test_ratify_decided_at_is_utc(env):
    env.gov.write(leaves=[_leaf()])
    assert _ratify(env)["event"]["decided_at"].endswith("+00:00")


def test_ratify_insufficient_evidence_does_not_write(env):
    env.gov.write(leaves=[_leaf(entity="ABAP_CDS.OTRO")])
    with pytest.raises(ops.ContractEvidenceInsufficient):
        _ratify(env)
    assert env.bucket.objects == {}                                # fail-closed: nada persistido


def test_ratify_unverifiable_evidence_does_not_write(env):
    env.gov.write_raw(name="broken.json")
    with pytest.raises(ops.ContractEvidenceUnverifiable):
        _ratify(env)
    assert env.bucket.objects == {}


def test_ratify_idempotent_same_request_id(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env, rationale="motivo", request_id="req-1")
    n = len(env.bucket.objects)
    out2 = _ratify(env, rationale="motivo", now=LATER, event_id="gd-OTRO", request_id="req-1")
    assert out2["outcome"] == "IDEMPOTENT"
    assert len(env.bucket.objects) == n                            # no crea evento nuevo


def test_ratify_idempotency_conflict_different_content(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env, rationale="motivo A", request_id="req-1")
    with pytest.raises(ops.IdempotencyConflict):
        _ratify(env, rationale="motivo DISTINTO", now=LATER, event_id="gd-2", request_id="req-1")


def test_request_id_reused_across_ratified_and_revoked_conflicts(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env, request_id="shared")
    with pytest.raises(ops.IdempotencyConflict):                   # mismo request_id de un RATIFIED, ahora para REVOKED
        ops.revoke(client="Kensing", identity=IDENT, rationale="x", actor="owner@danwor.com",
                   now=LATER, event_id="gd-2", request_id="shared")


def test_ratify_storage_unconfirmed_never_success(env):
    env.gov.write(leaves=[_leaf()])
    env.bucket.corrupt_puts = True
    with pytest.raises(store_exc := __import__("webapp.services.governed_decisions_store", fromlist=["StorageUnconfirmed"]).StorageUnconfirmed):
        _ratify(env)


def test_retry_after_confirmed_write_is_idempotent(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env, request_id="req-1")
    # reintento tras timeout del cliente pero con la escritura YA confirmada → idempotente, sin evento nuevo
    n = len(env.bucket.objects)
    out = _ratify(env, now=LATER, event_id="gd-reintento", request_id="req-1")
    assert out["outcome"] == "IDEMPOTENT" and len(env.bucket.objects) == n


def test_retry_after_failed_confirmation_then_succeeds(env):
    env.gov.write(leaves=[_leaf()])
    env.bucket.corrupt_puts = True
    with pytest.raises(Exception):
        _ratify(env, request_id="req-1")                          # 1er intento: confirmación falla → NO éxito
    assert env.bucket.objects == {} or all(b == b"CORRUPTO" for b in env.bucket.objects.values())
    env.bucket.objects.clear()
    env.bucket.corrupt_puts = False
    out = _ratify(env, request_id="req-1")                         # reintento con mismo request_id → ahora persiste limpio
    assert out["outcome"] == "RATIFIED"


def test_revoke_vigente(env):
    env.gov.write(leaves=[_leaf()])
    rat = _ratify(env)
    out = ops.revoke(client="Kensing", identity=IDENT, rationale="se corrige", actor="owner@danwor.com",
                     now=LATER, event_id="gd-2", request_id="rev-1")
    assert out["outcome"] == "REVOKED" and out["revoked_event_id"] == rat["event"]["event_id"]
    assert ops.status("Kensing", IDENT, LATER)["status"]["decision"] == gd.DECISION_REVOKED


def test_revoke_without_ratification_rejected(env):
    env.gov.write(leaves=[_leaf()])
    with pytest.raises(ops.RevocationNotAllowed):
        ops.revoke(client="Kensing", identity=IDENT, rationale="x", actor="owner@danwor.com",
                   now=NOW, event_id="gd-1", request_id="rev-1")


def test_revoke_repeated_new_request_id_rejected(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    ops.revoke(client="Kensing", identity=IDENT, rationale="corrige", actor="owner@danwor.com",
               now=LATER, event_id="gd-2", request_id="rev-1")
    with pytest.raises(ops.RevocationNotAllowed):                  # ya revocado, request_id NUEVO → rechazo
        ops.revoke(client="Kensing", identity=IDENT, rationale="otra vez", actor="owner@danwor.com",
                   now=LATER2, event_id="gd-3", request_id="rev-2")


def test_revoke_idempotent_same_request_id(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    first = ops.revoke(client="Kensing", identity=IDENT, rationale="se corrige", actor="owner@danwor.com",
                       now=LATER, event_id="gd-2", request_id="rev-1")
    n = len(env.bucket.objects)
    retry = ops.revoke(client="Kensing", identity=IDENT, rationale="se corrige", actor="owner@danwor.com",
                       now=LATER, event_id="gd-OTRO", request_id="rev-1")
    assert retry["outcome"] == "IDEMPOTENT"
    assert retry["revoked_event_id"] == first["revoked_event_id"]
    assert len(env.bucket.objects) == n                           # no crea evento nuevo


def test_revoke_idempotency_conflict_different_rationale(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    ops.revoke(client="Kensing", identity=IDENT, rationale="motivo A", actor="owner@danwor.com",
               now=LATER, event_id="gd-2", request_id="rev-1")
    with pytest.raises(ops.IdempotencyConflict):
        ops.revoke(client="Kensing", identity=IDENT, rationale="motivo DISTINTO", actor="owner@danwor.com",
                   now=LATER2, event_id="gd-3", request_id="rev-1")


def test_reratify_after_revoke_allowed(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    ops.revoke(client="Kensing", identity=IDENT, rationale="corrige", actor="owner@danwor.com",
               now=LATER, event_id="gd-2", request_id="rev-1")
    out = _ratify(env, rationale="re-ratifica", now=LATER2, event_id="gd-3", request_id="req-3")
    assert out["outcome"] == "RATIFIED"
    assert ops.status("Kensing", IDENT, datetime(2026, 10, 20, tzinfo=timezone.utc))["status"]["decision"] == gd.DECISION_RATIFIED


def test_reratify_vigente_allowed_new_request_id(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    out = _ratify(env, rationale="re-afirma explícita", now=LATER, event_id="gd-2", request_id="req-2")
    assert out["outcome"] == "RATIFIED"


def test_history_equal_timestamps_deterministic(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env, now=NOW, event_id="gd-a", request_id="r-a")
    # segunda ratificación con el MISMO decided_at (mismo now) pero distinto event_id → desempate determinista por event_id
    _ratify(env, rationale="re", now=NOW, event_id="gd-b", request_id="r-b")
    st1 = ops.status("Kensing", IDENT, LATER)["status"]
    st2 = ops.status("Kensing", IDENT, LATER)["status"]
    assert st1 == st2 and st1["decision"] == gd.DECISION_RATIFIED   # replay estable


# ═══════════════════════════ consulta técnica ═══════════════════════════

def test_status_three_independent_dimensions(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    st = ops.status("Kensing", IDENT, LATER)["status"]
    assert set(st) >= {"decision", "evidence", "review", "principal"}
    assert st["decision"] == gd.DECISION_RATIFIED and st["evidence"] == gd.EVIDENCE_MATCH


def test_status_evidence_unknown_when_absent(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    # elimina la evidencia → el digest ya no se resuelve
    import shutil
    shutil.rmtree(env.root)
    out = ops.status("Kensing", IDENT, LATER)
    assert out["evidence_digest"] is None
    assert out["status"]["evidence"] == gd.EVIDENCE_UNKNOWN
    assert out["status"]["decision"] == gd.DECISION_RATIFIED        # la decisión no se pierde


def test_status_unverifiable_evidence_does_not_break_query(env):
    env.gov.write(leaves=[_leaf()])
    _ratify(env)
    env.gov.write_raw(name="broken.json")                          # evidencia ahora ilegible
    out = ops.status("Kensing", IDENT, LATER)
    assert out["status"]["evidence"] == gd.EVIDENCE_UNKNOWN         # consulta no se rompe
    assert out["status"]["decision"] == gd.DECISION_RATIFIED


def test_status_empty_history_is_none(env):
    env.gov.write(leaves=[_leaf()])
    out = ops.status("Kensing", IDENT, NOW)
    assert out["status"]["decision"] == gd.DECISION_NONE and out["events"] == []


# ═══════════════════════════ independencia de Judgment / Assessments ═══════════════════════════

def test_reads_evidence_but_never_mutates_store(env):
    env.gov.write(leaves=[_leaf()])
    import copy
    before = {p: p.read_bytes() for p in (env.root / "v1").rglob("*.json")}
    _ratify(env)
    after = {p: p.read_bytes() for p in (env.root / "v1").rglob("*.json")}
    assert before == after                                          # SOLO LECTURA de Assessments (D8); no reescribe sobres
