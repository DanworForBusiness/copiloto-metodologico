"""P1 · lectores durante la activación y la recuperación: Catálogo en frío, errores 503 reintentables, verificación de coherencia al arrancar."""
from __future__ import annotations

import threading
from functools import lru_cache

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import webapp.main as main
from core import publication_gate as gate
from tests.publication_fakes import CLIENT
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import publication_txn as txn
from webapp.services import assessment_service as assess
from webapp.services import catalog_reads
from webapp.services import datasphere_publication_service as pub


@pytest.fixture()
def world(ctx, monkeypatch, tmp_path):                                                                     # noqa: F811
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    assess.reset_memory_cache()
    catalog_reads._catalog_entries.cache_clear()
    yield ctx
    gate.clear(ctx.catalog)
    catalog_reads._catalog_entries.cache_clear()
    assess.reset_memory_cache()


def _bg(fn):
    out: list = []
    t = threading.Thread(target=lambda: out.append(_catch(fn)))
    t.start()
    return t, out


def _catch(fn):
    try:
        return fn()
    except Exception as exc:                                                                                # noqa: BLE001
        return exc


def test_a_cold_catalog_read_waits_for_the_activation_but_a_warm_cache_serves_the_last_confirmed_version(world, monkeypatch):
    loads: list = []

    @lru_cache(maxsize=1)
    def fake_cached():
        loads.append(1)
        return ("modulo", ["entrada"])

    monkeypatch.setattr(catalog_reads, "_catalog_entries_cached", fake_cached)       # el cargador real depende de BASE_DIR; acá se prueba la COMPUERTA, no la carga
    assert catalog_reads._catalog_entries()[1] == ["entrada"]                       # caché caliente: la última versión confirmada
    gate.begin("opX", CLIENT, world.catalog)
    try:
        assert catalog_reads._catalog_entries()[1] == ["entrada"]                   # caliente: NO espera
        fake_cached.cache_clear()                                                   # frío (p. ej. recién invalidada)
        monkeypatch.setattr(gate, "DEFAULT_WAIT_S", 0.2)
        monkeypatch.setenv("DANWOR_CATALOG_DIR", str(world.catalog))
        t, out = _bg(catalog_reads._catalog_entries)
        t.join(5)
        assert isinstance(out[0], gate.PublicationInProgress) and loads == [1]      # una lectura en frío NO carga un Catálogo a medio escribir
    finally:
        gate.end("opX", world.catalog)
    assert catalog_reads._catalog_entries()[1] == ["entrada"] and loads == [1, 1]


def test_the_cache_clear_of_the_catalog_reader_is_still_reachable_for_the_hub():
    assert callable(catalog_reads._catalog_entries.cache_clear) and callable(catalog_reads._catalog_entries.cache_info)


def test_the_web_answers_503_with_retry_after_while_a_version_is_being_activated_or_is_unresolved():
    app = FastAPI()
    for handler_exc, handler in ((gate.PublicationInProgress, main._publication_in_progress), (gate.PublicationBlocked, main._publication_blocked)):
        app.add_exception_handler(handler_exc, handler)

    @app.get("/en-curso")
    def en_curso():
        raise gate.PublicationInProgress(retry_after=7)

    @app.get("/bloqueado")
    def bloqueado():
        raise gate.PublicationBlocked("op1", "Acme")

    client = TestClient(app)
    a, b = client.get("/en-curso"), client.get("/bloqueado")
    assert (a.status_code, a.headers["retry-after"]) == (503, "7") and "publicando" in a.text
    assert b.status_code == 503 and b.headers["retry-after"] == "30" and "verificación" in b.text
    handlers = main.app.exception_handlers
    assert gate.PublicationInProgress in handlers and gate.PublicationBlocked in handlers                    # y están registrados en la aplicación real


def test_boot_verification_is_silent_for_a_coherent_disk_and_reports_a_mixture(world, monkeypatch, caplog):
    monkeypatch.setenv(txn.SAFE_ENV, "1")
    assert world.publish(stage=True)[0].ok
    assert pub.verify_ledgers() == {}                                                                       # lo restaurado ES una versión publicada
    (world.pool / CLIENT / "views" / "3VF_LP.json").write_text("{}", encoding="utf-8")                      # un restore desde un Spaces a medias: mezcla
    mismatches = pub.verify_ledgers()
    assert list(mismatches) == [CLIENT] and any("3VF_LP" in p for p in mismatches[CLIENT])
    with caplog.at_level("ERROR", logger="danwor.datasphere_publication"):
        pub.boot_recover()
    assert any("NO coincide con su última publicación" in r.getMessage() for r in caplog.records)


def test_boot_recovery_does_nothing_with_the_switch_off(world, monkeypatch):
    monkeypatch.delenv(txn.SAFE_ENV, raising=False)
    called: list = []
    monkeypatch.setattr(pub, "resolve_pending", lambda *a, **k: called.append(1))
    pub.boot_recover()
    assert called == []


def test_resolve_pending_never_raises_and_discards_caches_when_something_was_resolved(world, monkeypatch):
    monkeypatch.setattr(txn, "recover_pending", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin candado")))
    assert pub.resolve_pending(world.catalog) == []
    cleared: list = []
    monkeypatch.setattr(txn, "recover_pending", lambda *a, **k: [{"operation_id": "o", "from": "COMMIT_UNCERTAIN", "action": "CONFIRMED"}])
    monkeypatch.setattr(pub.republication_stage.AssessmentStage, "finished", lambda self, ctx: cleared.append(1))
    assert pub.resolve_pending(world.catalog)[0]["action"] == "CONFIRMED" and cleared == [1]
