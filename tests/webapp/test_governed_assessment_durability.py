"""
test_governed_assessment_durability.py — el Assessment GOBERNADO del Catálogo es DURABLE; el AD-HOC sigue efímero.

Contrato (ver `assessment_service`: "ALMACÉN DURABLE"):
  * `generate` (Publicar → Assessment y "Generar informe": MISMA función) escribe el sobre canónico bajo
    `governed-assessments/v1/<cliente>/<AM>__<tag>.json`, directorio registrado en PERSIST_PATHS (lo restaura/sube `core.remote_store`);
  * tras un reinicio (RAM vacía) el assessment se recupera del almacén; la vigencia se re-evalúa (firma del pool + versiones), jamás CURRENT a ciegas;
  * el Portfolio y el detalle del Catálogo lo leen sin cambios; un archivo corrupto/ajeno/de otra identidad falla CERRADO;
  * el ad-hoc NO pasa por acá; no se serializan credenciales; un fallo de Spaces no corrompe lo bueno ni se informa como éxito;
  * el backfill histórico lo materializa durablemente y no regenera lo ya existente.
Fixtures SINTÉTICAS y un Spaces falso en memoria (sin red).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from core import remote_store
from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog import catalog_service as cs
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as pf
from webapp.services import datasphere_publication_service as dsp

REPO = Path(__file__).resolve().parents[2]
ACME = ["1VR_RAW", "3VF_P", "4AM_ACME", "RAW_T"]
ORIGIN = {"Origin": "http://testserver"}
HX = {"HX-Request": "true"}


def _flow(cliente, artefactos, eid, dominio="Dom"):
    return cs.CatalogEntry(path="x", cliente=cliente, dominio=dominio, veredicto="buena", apto="ok", entry_id=eid, artefactos=list(artefactos))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    inbound = write_inbound(tmp_path / "pool")
    governed = tmp_path / "governed-assessments"
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(governed))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "runtime-cache")
    entries = [_flow("Acme", ACME, "acme-4am"), _flow("Solo", ["1VR_ONLY", "LOCAL_X"], "solo-na")]
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    svc.reset_memory_cache()
    svc._validated.clear()
    svc.invalidate_pool_index()
    yield SimpleNamespace(inbound=inbound, governed=governed, entries=entries, acme=entries[0], tmp=tmp_path)
    svc.reset_memory_cache()
    svc.invalidate_pool_index()


def restart():
    """Simula un reinicio del proceso: se pierde TODA la RAM del servicio de assessment."""
    svc.reset_memory_cache()
    svc._validated.clear()
    svc.invalidate_pool_index()


def files(w):
    return sorted(w.governed.rglob("*.json")) if w.governed.exists() else []


def client_for():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def no_engine(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("el motor NO debe ejecutarse al leer el estado")
    monkeypatch.setattr(svc, "_engine_assess", boom)


# ── Spaces falso en memoria (reusa `core.remote_store` real: restore / push_path / mirror_up) ──
class FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_uploads = False

    def upload_file(self, filename, bucket, key):
        if self.fail_uploads:
            raise RuntimeError("simulated upload failure")
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket, key, dest):
        Path(dest).write_bytes(self.objects[key])

    def get_paginator(self, name):
        return self

    def paginate(self, Bucket, Prefix):
        return [{"Contents": [{"Key": k} for k in self.objects if k.startswith(Prefix)]}]


@pytest.fixture()
def spaces(world, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(config, "SPACES_ENDPOINT", "https://fake.digitaloceanspaces.com")
    monkeypatch.setattr(config, "SPACES_BUCKET", "fake-bucket")
    monkeypatch.setattr(config, "SPACES_KEY", "fake-key")
    monkeypatch.setattr(config, "SPACES_SECRET", "fake-secret")
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "BASE_DIR", world.tmp)
    monkeypatch.setattr(config, "PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME])
    monkeypatch.setattr(remote_store, "_get_client", lambda: bucket)
    monkeypatch.setattr(remote_store, "_signatures", {})
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    return bucket


# ── 1-6. persistencia y recuperación tras reinicio ──────────────────────────
def test_governed_assessment_is_persisted_after_generation_and_recovered_after_restart(world, monkeypatch):
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT and st.durable == svc.DURABLE_LOCAL                   # sin Spaces configurado: disco local
    (path,) = files(world)
    assert path.relative_to(world.governed).parts[:2] == ("v1", "Acme") and path.name.startswith("4AM_ACME__")
    env = json.loads(path.read_text(encoding="utf-8"))
    assert env["envelope_version"] == svc.ENVELOPE_VERSION and env["status"] == "OK" and env["terminal_am"] == "4AM_ACME"
    restart()                                                                           # 2-3. se limpia TODA la memoria del servicio
    no_engine(monkeypatch)
    again = svc.status_for_entry(world.acme)                                            # 4-5. se recupera del almacén, sin motor
    assert again.state == svc.CURRENT and again.state != svc.NOT_YET_ASSESSED
    assert again.profile == st.profile and again.generated_at == st.generated_at


def test_currentness_is_still_evaluated_after_restore(world):
    svc.generate(world.acme)
    restart()
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A7"', 1), encoding="utf-8")   # el pool cambió mientras el proceso estaba caído
    assert svc.status_for_entry(world.acme).state == svc.STALE                                # jamás CURRENT a ciegas


def test_portfolio_sees_the_restored_assessment(world):
    svc.generate(world.acme)
    restart()
    snap = pf.build_snapshot(world.acme, "datasphere_core")
    assert snap["client"] == "Acme" and snap["states"][svc.CURRENT] == 1 and snap["states"][svc.NOT_YET_ASSESSED] == 0
    assert snap["summary"]["current_assessments"] == 1 and snap["assets"][0]["state"] == svc.CURRENT


# ── 7-8. ambas vías de escritura producen el MISMO registro durable ─────────
def _manual_generate(world) -> dict:
    r = client_for().post("/catalog/flow/assessment?flow=acme-4am", headers={**ORIGIN, **HX})
    assert r.status_code == 200 and len(files(world)) == 1
    return json.loads(files(world)[0].read_text(encoding="utf-8"))


def test_manual_generate_informe_persists_it(world):
    _manual_generate(world)
    restart()
    assert svc.status_for_entry(world.acme).state == svc.CURRENT


def test_publish_to_assessment_bridge_persists_the_same_record(world):
    manual = _manual_generate(world)
    for p in files(world):
        p.unlink()
    restart()
    out = dsp.register_assessment(SimpleNamespace(client="Acme", root_object="4AM_ACME"))      # el puente de Publicar
    assert out == [("acme-4am", svc.CURRENT)] and len(files(world)) == 1
    bridge = json.loads(files(world)[0].read_text(encoding="utf-8"))
    for k in ("envelope_version", "client", "terminal_am", "assessment_schema_version", "assessment_versions", "input_pool_signature",
              "assessment_artifact_signature", "status", "profile_json", "error_code"):
        assert bridge[k] == manual[k]                                                          # mismo registro canónico (salvo la hora de generación)
    assert set(bridge) == set(manual)


# ── 9. el ad-hoc NO se persiste ─────────────────────────────────────────────
def test_adhoc_assessment_never_touches_the_durable_store(world, monkeypatch):
    from tools.catalog.modeling_assessment import shadow as ma_shadow
    monkeypatch.setattr(svc, "_store_envelope", lambda env: (_ for _ in ()).throw(AssertionError("el ad-hoc no usa el almacén gobernado")))
    doc, code = svc.assess_evidence_root(world.inbound, "Acme", "4AM_ACME", "General")
    assert code is None and doc is not None                                                    # evalúa con el mismo motor, sin persistir
    assert files(world) == [] and svc._memory == {}
    adhoc = Path(config.ASSESSMENT_CACHE_DIR)
    assert config.GOVERNED_ASSESSMENTS_NAME not in adhoc.parts and "assessment_cache" not in config.PERSIST_PATHS
    assert svc.governed_dir().resolve() not in (adhoc / "adhoc").resolve().parents and ma_shadow is not None


# ── 10. backfill ────────────────────────────────────────────────────────────
def test_backfill_materializes_durably_and_does_not_regenerate_after_restart(world, monkeypatch):
    out = bf.backfill(world.entries)
    assert out["counts"] == {"generated": 1, "skipped": 1, "failed": 0}                        # Solo = NO APLICA
    assert out["durability"] == {"remote": 0, "local": 1, "memory": 0} and len(files(world)) == 1
    restart()
    monkeypatch.setattr(svc, "generate", lambda *a, **k: pytest.fail("no se regenera lo ya persistido"))
    again = bf.backfill(world.entries)
    assert again["counts"] == {"generated": 0, "skipped": 2, "failed": 0}
    assert [x["state"] for x in again["skipped"] if x["entry_id"] == "acme-4am"] == [svc.CURRENT]


def test_backfill_reports_a_memory_only_result_as_a_failure(world, monkeypatch):
    monkeypatch.setattr(svc, "governed_path", lambda *a: None)                                  # no hay dónde persistir
    out = bf.backfill([world.acme])
    assert out["counts"]["generated"] == 0 and out["counts"]["failed"] == 1 and "no persistido" in out["failed"][0]["reason"]


# ── 11. falla cerrado ───────────────────────────────────────────────────────
def test_corrupt_foreign_or_other_identity_durable_files_fail_closed(world):
    svc.generate(world.acme)
    path = files(world)[0]
    good = json.loads(path.read_text(encoding="utf-8"))
    other = dict(good, terminal_am="4AM_OTRO")
    for junk in ("no es json {", json.dumps({"otra": "cosa"}), json.dumps([1]), json.dumps(other)):
        path.write_text(junk, encoding="utf-8")
        restart()
        assert svc.status_for_entry(world.acme).state == svc.NOT_YET_ASSESSED


def test_incompatible_persisted_schema_is_reported_not_rendered(world):
    svc.generate(world.acme)
    path = files(world)[0]
    env = json.loads(path.read_text(encoding="utf-8"))
    env["assessment_schema_version"] = "assessment/v0-obsoleto"
    path.write_text(json.dumps(env), encoding="utf-8")
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.INCOMPATIBLE_SCHEMA and st.profile is None


# ── 12. sin secretos ────────────────────────────────────────────────────────
def test_no_credentials_or_request_data_are_serialized(world):
    svc.generate(world.acme)
    env = json.loads(files(world)[0].read_text(encoding="utf-8"))
    assert set(env) == {"envelope_version", "client", "terminal_am", "generated_at", "assessment_schema_version", "assessment_versions",
                        "input_pool_signature", "assessment_artifact_signature", "status", "profile_json", "judgment", "error_code"}
    text = files(world)[0].read_text(encoding="utf-8")
    assert not re.search(r"secret|password|token|authorization|bearer|api[_-]?key|credential|t@d\.com", text, re.I)


# ── Spaces: restore / write-through / fallos ────────────────────────────────
def test_write_through_and_restore_from_spaces_after_a_deploy(world, spaces):
    st = svc.generate(world.acme)
    assert st.durable == svc.DURABLE_REMOTE
    key = "governed-assessments/v1/Acme/" + files(world)[0].name
    assert key in spaces.objects
    for p in files(world):                                                                  # deploy: FS efímero vacío
        p.unlink()
    restart()
    assert svc.status_for_entry(world.acme).state == svc.NOT_YET_ASSESSED                    # antes del restore no hay nada local
    assert remote_store.restore() == 1                                                      # boot: baja lo persistido (mecanismo existente)
    assert svc.status_for_entry(world.acme).state == svc.CURRENT


def test_a_failed_spaces_sync_never_corrupts_the_good_record_nor_claims_success(world, spaces):
    svc.generate(world.acme)
    good = files(world)[0].read_text(encoding="utf-8")
    spaces.fail_uploads = True
    f = world.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A5"', 1), encoding="utf-8")   # el pool cambió: STALE → regenerar
    restart()
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT and st.durable == svc.DURABLE_LOCAL                        # NO se informa como remoto
    new = files(world)[0].read_text(encoding="utf-8")
    assert json.loads(new)["input_pool_signature"] != json.loads(good)["input_pool_signature"]    # el local quedó consistente y completo
    assert json.loads(new)["assessment_artifact_signature"]
    spaces.fail_uploads = False
    assert remote_store.mirror_up() >= 1                                                      # el ciclo periódico reintenta lo pendiente
    assert json.loads(next(iter(spaces.objects.values())))["input_pool_signature"] == json.loads(new)["input_pool_signature"]


def test_a_directory_missing_from_persist_paths_is_reported_as_local_only(world, spaces, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_PATHS", ["reports"])                                # el entorno no sumó 'governed-assessments'
    assert svc.generate(world.acme).durable == svc.DURABLE_LOCAL and spaces.objects == {}


def test_reads_wait_once_for_the_boot_restore_instead_of_concluding_no_assessment(world, monkeypatch):
    waited = []
    monkeypatch.setattr(remote_store, "_restore_state", "in_progress")
    monkeypatch.setattr(remote_store, "await_restore", lambda t: waited.append(t) or "completed")
    svc.statuses_for_entries(world.entries)
    assert waited == [svc._RESTORE_WAIT_S]                                                   # acotado y una sola vez por lote


def test_governed_dir_is_registered_for_persistence_and_never_under_the_catalog(world, monkeypatch):
    assert config.GOVERNED_ASSESSMENTS_NAME in config.PERSIST_PATHS
    assert (config.GOVERNED_ASSESSMENTS_NAME, world.governed) in remote_store._persist_dirs()     # remote_store lo restaura/sube con su override
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(world.tmp / "catalog" / "gov"))
    assert svc.governed_dir() is None and svc.generate(world.acme).durable == svc.DURABLE_MEMORY   # fail-closed: bajo el catálogo no se escribe
    assert not (world.tmp / "catalog" / "gov").exists()
