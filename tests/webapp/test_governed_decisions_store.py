"""Tests focalizados del almacén append-only `governed_decisions_store` (v4.B / B2). Spaces FALSO en memoria, sin conexión real.

Cubre: escritura create-only + confirmación por bytes, idempotencia, colisión/reuso de id (no sobrescribe), replay
DETERMINISTA independiente del orden de Spaces, RATIFIED+REVOKED, integridad (objeto corrupto/subject cruzado/contradicción/
revocación huérfana → fail-closed), namespace fuera de PERSIST_PATHS, gating de escritura, y ausencia de rutas update/delete.
No toca Judgment/evidencia/Assessments."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
from core import remote_store
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_store as gds


# ─────────────────────────── Spaces falso ───────────────────────────

class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


class FakeSpaces:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls = 0
        self.corrupt_puts = False      # simula "confirma pero guarda otra cosa"
        self.fail_puts = False         # simula fallo de almacenamiento

    def put_object(self, Bucket, Key, Body):
        self.put_calls += 1
        if self.fail_puts:
            raise RuntimeError("fallo simulado de almacenamiento")
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


@pytest.fixture()
def spaces(monkeypatch):
    bucket = FakeSpaces()
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-assessments"])   # NO incluye governed-decisions
    monkeypatch.setattr(remote_store, "_get_client", lambda: bucket)
    return bucket


# ─────────────────────────── helpers de eventos ───────────────────────────

def _ce() -> dict:
    return {"version": "canonical-evidence/v4",
            "published_contract": {"fields": {"fields": [{"technical_name": "A"}], "counts": {"fields": 1}}},
            "source_contract_classes": {"leaves": [{"leaf": "ZA", "contract_class": "GOVERNED_CDS"}]}}


SUBJ = {"client": "Kensing", "technical_id": "ZCDS_BILLING"}


def _rat(event_id="e1", decided_at="2026-10-17T12:00:00+00:00", rationale="contrato gobernado", ce=None) -> dict:
    return gd.build_ratification(event_id=event_id, subject=SUBJ, canonical_evidence=ce or _ce(),
                                 rationale=rationale, actor="joconnor@danwor.com", decided_at=decided_at)


def _rev(event_id="r1", revokes="e1", decided_at="2026-10-18T12:00:00+00:00") -> dict:
    return gd.build_revocation(event_id=event_id, subject=SUBJ, revokes_event_id=revokes, rationale="error", actor="joconnor@danwor.com", decided_at=decided_at)


def _now(s="2026-10-17T18:00:00Z"):
    return gd.parse_utc(s)


# ─────────────────────────── escritura + confirmación ───────────────────────────

def test_write_then_read_confirms_bytes(spaces):
    ev = _rat()
    out = gds.append_event(ev)
    assert out["stored"] is True and out["idempotent"] is False
    assert remote_store.get_governed_decision_object(out["key"]) == gd.canonical_json(ev).encode("utf-8")


def test_idempotent_rewrite_same_event(spaces):
    ev = _rat()
    gds.append_event(ev)
    before = dict(spaces.objects)
    out2 = gds.append_event(ev)
    assert out2["stored"] is False and out2["idempotent"] is True
    assert spaces.objects == before                        # no se reescribe


def test_id_reuse_with_different_content_is_rejected(spaces):
    gds.append_event(_rat())
    before = dict(spaces.objects)
    with pytest.raises(gds.DecisionConflict):
        gds.append_event(_rat(rationale="OTRO motivo"))     # misma clave (mismo event_id+decided_at), bytes distintos
    assert spaces.objects == before                        # jamás sobrescribe


def test_storage_error_never_returns_success(spaces):
    spaces.corrupt_puts = True
    with pytest.raises(gds.StorageUnconfirmed):
        gds.append_event(_rat())                            # confirma por bytes ⇒ falla, no "éxito"


def test_put_failure_propagates_without_success(spaces):
    spaces.fail_puts = True
    with pytest.raises(Exception):
        gds.append_event(_rat())
    assert spaces.objects == {}                             # nada quedó escrito


# ─────────────────────────── reconstrucción / replay ───────────────────────────

def test_reconstruct_ratified(spaces):
    ev = _rat()
    gds.append_event(ev)
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(_ce()), _now())
    assert st["status"]["decision"] == "RATIFIED" and st["status"]["evidence"] == "MATCH"
    assert len(st["events"]) == 1


def test_replay_is_deterministic_regardless_of_spaces_order(spaces, monkeypatch):
    # tres eventos en el tiempo: RATIFIED(T1) → REVOKED(T2) → RATIFIED(T3)
    gds.append_event(_rat(event_id="e1", decided_at="2026-10-17T12:00:00+00:00"))
    gds.append_event(_rev(event_id="r1", revokes="e1", decided_at="2026-10-18T12:00:00+00:00"))
    gds.append_event(_rat(event_id="e2", decided_at="2026-10-19T12:00:00+00:00"))
    # Spaces devuelve las claves en orden INVERSO: el replay NO debe depender de eso
    real = remote_store.list_governed_decision_keys
    monkeypatch.setattr(remote_store, "list_governed_decision_keys", lambda p: list(reversed(real(p))))
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(_ce()), _now("2026-10-20T00:00:00Z"))
    assert [e["event_id"] for e in st["events"]] == ["e1", "r1", "e2"]      # orden por contenido (decided_at, event_id)
    assert st["status"]["decision"] == "RATIFIED" and st["status"]["ratified_event_id"] == "e2"


def test_ratified_then_revoked(spaces):
    gds.append_event(_rat())
    gds.append_event(_rev())
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(_ce()), _now("2026-10-19T00:00:00Z"))
    assert st["status"]["decision"] == "REVOKED" and st["status"]["principal"] == "REVOKED"


def test_evidence_stale_after_contract_change(spaces):
    gds.append_event(_rat())
    changed = _ce(); changed["published_contract"]["fields"]["fields"][0]["technical_name"] = "B"
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(changed), _now())
    assert st["status"]["evidence"] == "STALE" and st["status"]["decision"] == "RATIFIED"


# ─────────────────────────── integridad (fail-closed) ───────────────────────────

def test_corrupt_object_fails_closed(spaces):
    ev = _rat()
    gds.append_event(ev)
    # corromper el objeto almacenado (no-JSON)
    (key,) = list(spaces.objects)
    spaces.objects[key] = b"no-json {{{"
    with pytest.raises(gds.HistoryIntegrityError):
        gds.reconstruct("Kensing", "ZCDS_BILLING", None, _now())


def test_crossed_subject_fails_closed(spaces):
    # escribir, bajo el prefijo de SUBJ, un evento cuyo subject es OTRO
    other = gd.build_ratification(event_id="x", subject={"client": "Kensing", "technical_id": "OTRO"},
                                  canonical_evidence=_ce(), rationale="x", actor="a@d.com", decided_at="2026-10-17T12:00:00+00:00")
    bogus_key = gds.subject_prefix(SUBJ) + "20261017T120000Z-x.json"
    remote_store.put_governed_decision_object(bogus_key, gd.canonical_json(other).encode("utf-8"))
    with pytest.raises(gds.HistoryIntegrityError):
        gds.load_events("Kensing", "ZCDS_BILLING")


def test_duplicate_event_id_divergent_content_fails_closed(spaces):
    gds.append_event(_rat(event_id="e1", decided_at="2026-10-17T12:00:00+00:00"))
    # mismo event_id, OTRO decided_at (⇒ otra clave) y contenido divergente
    gds.append_event(_rat(event_id="e1", decided_at="2026-10-18T12:00:00+00:00", rationale="divergente"))
    with pytest.raises(gds.HistoryIntegrityError):
        gds.reconstruct("Kensing", "ZCDS_BILLING", None, _now("2026-10-19T00:00:00Z"))


def test_revocation_without_ratified_fails_closed(spaces):
    gds.append_event(_rev(event_id="r1", revokes="no-existe"))
    with pytest.raises(gds.HistoryIntegrityError):
        gds.reconstruct("Kensing", "ZCDS_BILLING", None, _now("2026-10-19T00:00:00Z"))


# ─────────────────────────── límite real de concurrencia (no se oculta) ───────────────────────────

def test_create_only_is_check_then_put_not_atomic(spaces, monkeypatch):
    """Demuestra el LÍMITE REAL (sin ocultarlo con el fake): check-then-PUT NO impide una sobrescritura concurrente.
    DigitalOcean Spaces no ofrece PUT condicional ⇒ bajo una ventana de carrera (el check ve la clave ausente), el PUT crudo
    sobrescribe. B2 NO garantiza exclusividad atómica; sí confirmación por bytes e inmutabilidad lógica en operación normal."""
    evA = _rat(event_id="race", rationale="A")
    gds.append_event(evA)
    key = gds.event_key(evA)
    evB = _rat(event_id="race", rationale="B")                 # MISMA clave (mismo event_id+decided_at), bytes distintos
    assert gds.event_key(evB) == key
    # ventana de carrera: el 2º escritor hizo su check cuando la clave aún no existía (vista "ausente")
    real_get = remote_store.get_governed_decision_object
    monkeypatch.setattr(remote_store, "get_governed_decision_object", lambda rel: None)
    remote_store.put_governed_decision_object(key, gd.canonical_json(evB).encode("utf-8"))
    monkeypatch.setattr(remote_store, "get_governed_decision_object", real_get)   # restaurar SOLO este stub (no el fixture)
    # el PUT crudo SOBRESCRIBIÓ a evA (se perdió): la exclusividad atómica NO está garantizada por el mecanismo.
    assert remote_store.get_governed_decision_object(key) == gd.canonical_json(evB).encode("utf-8")
    # Mitigación en la práctica: clave única por evento (event_id robusto de B3) + rechazo secuencial del reuso
    # (test_sequential_id_reuse_is_rejected_not_silent) + detección de contradicciones en el replay.


def test_sequential_id_reuse_is_rejected_not_silent(spaces):
    gds.append_event(_rat(event_id="race", rationale="A"))
    with pytest.raises(gds.DecisionConflict):
        gds.append_event(_rat(event_id="race", rationale="B"))


# ─────────────────────────── consistencia del historial (políticas) ───────────────────────────

def test_successive_ratifications_latest_is_active(spaces):
    gds.append_event(_rat(event_id="e1", decided_at="2026-10-17T12:00:00+00:00"))
    gds.append_event(_rat(event_id="e2", decided_at="2026-10-19T12:00:00+00:00"))
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(_ce()), _now("2026-10-20T00:00:00Z"))
    # política determinada por B1 derive_status: la ratificación MÁS RECIENTE es la vigente
    assert st["status"]["decision"] == "RATIFIED" and st["status"]["ratified_event_id"] == "e2"
    assert [e["event_id"] for e in st["events"]] == ["e1", "e2"]


def test_equal_timestamps_deterministic_tiebreak_by_event_id(spaces):
    # mismo decided_at, distinto event_id ⇒ claves distintas; orden determinista por (decided_at, event_id)
    gds.append_event(_rat(event_id="eB", decided_at="2026-10-17T12:00:00+00:00"))
    gds.append_event(_rat(event_id="eA", decided_at="2026-10-17T12:00:00+00:00"))
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(_ce()), _now())
    assert [e["event_id"] for e in st["events"]] == ["eA", "eB"]          # desempate estable por event_id
    assert st["status"]["ratified_event_id"] == "eB"                      # el último en orden determinista


def test_repeated_revocation_is_tolerated_state_revoked(spaces):
    # revocar dos veces la MISMA ratificación: append-only, no rompe; el estado queda REVOKED.
    gds.append_event(_rat(event_id="e1", decided_at="2026-10-17T12:00:00+00:00"))
    gds.append_event(_rev(event_id="r1", revokes="e1", decided_at="2026-10-18T12:00:00+00:00"))
    gds.append_event(_rev(event_id="r2", revokes="e1", decided_at="2026-10-19T12:00:00+00:00"))
    st = gds.reconstruct("Kensing", "ZCDS_BILLING", gd.contract_evidence_digest(_ce()), _now("2026-10-20T00:00:00Z"))
    assert st["status"]["decision"] == "REVOKED"
    assert len(st["events"]) == 3                                         # auditoría completa conservada
    # NOTA: si "revocar una ratificación ya revocada / no vigente" debe rechazarse es una política PENDIENTE para B3;
    # B2 es permisivo (append-only) y no inventa regla de negocio.


# ─────────────────────────── namespace / gating / sin update-delete ───────────────────────────

def test_namespace_outside_persist_paths(spaces, monkeypatch):
    assert remote_store.governed_decisions_namespace_collision() is None
    monkeypatch.setattr(config, "PERSIST_PATHS", ["governed-decisions"])      # colisión → rechazo al escribir
    with pytest.raises(remote_store.RemoteReadError):
        gds.append_event(_rat())


def test_write_disabled_is_fail_closed(spaces, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    with pytest.raises(remote_store.RemoteReadError):
        gds.append_event(_rat())


def test_no_update_or_delete_paths_exist():
    for mod in (remote_store, gds):
        for name in dir(mod):
            low = name.lower()
            if "decision" in low or mod is gds:
                assert "delete" not in low and "update" not in low and "overwrite" not in low, name
    # el almacen no INVOCA rutas de borrado/actualización/sync (se chequean llamadas, no menciones en docstring)
    import inspect
    src = inspect.getsource(gds)
    for forbidden in ("delete_object(", "copy_object(", ".delete(", ".update(", "mirror_up(", "push_path(", "delete_path("):
        assert forbidden not in src, forbidden
