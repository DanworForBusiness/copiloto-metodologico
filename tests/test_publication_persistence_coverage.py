"""P1 · B2 — la publicación segura NO admite un assessment solo en disco efímero: valida la persistencia EFECTIVA antes de modificar nada.

Criterio del dueño: con ``DANWOR_PUBLICATION_SAFE`` encendido y el almacén ``governed-assessments`` sin cobertura durable real, la publicación aborta ANTES de tocar el Catálogo (y antes de
conectar con Datasphere). Se valida la configuración efectiva (modo, credenciales, escritura habilitada, confirmación remota pedida, rutas persistidas y el directorio al que de verdad
escribe el almacén), no la presencia textual de un nombre en una lista.

Frontera simulada: el tenant de Datasphere y el bucket. Todo bajo ``tmp_path``: jamás Spaces de producción.
"""
from __future__ import annotations

import hashlib

import pytest

import config
from core import publication_gate as gate
from core import remote_store
from tests.publication_fakes import FakeS3, enable_remote
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog import publication_remote as pr
from tools.catalog import publication_txn as txn
from webapp.services import assessment_service as assess
from webapp.services import datasphere_publication_service as svc

AM_A = "AM_Lista_Precios"


@pytest.fixture()
def world(ctx, monkeypatch, tmp_path):                                                                     # noqa: F811
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    monkeypatch.setenv(txn.SAFE_ENV, "1")
    assess.reset_memory_cache()
    assess.invalidate_pool_index()
    gate.set_resolver(None)
    ctx.governed = tmp_path / "governed"
    ctx.s3 = FakeS3()
    ctx.reads = []
    original = dsp.DatasphereCli.read_object
    monkeypatch.setattr(dsp.DatasphereCli, "read_object", lambda self, space, otype, name: ctx.reads.append(name) or original(self, space, otype, name))
    yield ctx
    gate.clear(ctx.catalog)
    assess.reset_memory_cache()
    assess.invalidate_pool_index()


def pub(w, **kw):
    kw.setdefault("mirror", True)
    return w.publish(AM_A, stage=True, **kw)


def disk(w) -> dict[str, str]:
    """Todo lo que hay bajo el Catálogo y el almacén gobernado (huella del contenido): lo que NO puede cambiar si la publicación aborta."""
    out = {}
    for base in (w.catalog.parent,):
        for p in sorted(base.rglob("*")):
            if p.is_file() and "locks" not in p.parts and "pytest" not in p.name:
                out[p.relative_to(base).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    return out


def assert_aborted_untouched(w, res, code):
    assert (res.status, res.error_code, res.ok) == (txn.FAILED_NO_CHANGE, code, False)
    assert res.stages == {"persistence": "FAILED"}                                                              # abortó ANTES de extraer, de aplicar y de confirmar
    assert w.reads == [] and w.s3.uploads == 0 and w.s3.log == []                                               # ni Datasphere ni Spaces vieron nada
    assert w.journals() == [] and gate.read_state(w.catalog) is None and not txn.has_pending(w.catalog)


# ═══════════════ el almacén gobernado no está cubierto ═══════════════
def test_a_governed_store_missing_from_the_persisted_paths_aborts_before_touching_anything(world, monkeypatch):
    enable_remote(monkeypatch, world.s3)                                                                        # PERSIST_PATHS = [catalog_local]: 'governed-assessments' NO figura
    before = disk(world)
    res, lines = pub(world)
    assert_aborted_untouched(world, res, "ASSESSMENT_NOT_PERSISTED")
    assert disk(world) == before and "SPACES_PERSIST_PATHS" in res.message
    assert any("ASSESSMENT_NOT_PERSISTED" in ln for ln in lines)


def test_the_abort_also_protects_an_already_published_version(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.governed)
    first, _ = pub(world)
    assert first.status == txn.COMPLETED
    before, remote_before = disk(world), dict(world.s3.objects)
    uploads, reads = world.s3.uploads, len(world.reads)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local"])                                             # un redeploy con SPACES_PERSIST_PATHS sin el almacén
    res, _ = pub(world)
    assert (res.status, res.error_code) == (txn.FAILED_NO_CHANGE, "ASSESSMENT_NOT_PERSISTED")
    assert disk(world) == before and world.s3.objects == remote_before and world.s3.uploads == uploads and len(world.reads) == reads


def test_the_textual_presence_of_the_name_is_not_enough_the_effective_directory_must_be_the_persisted_one(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.governed)                                               # el nombre SÍ figura en la lista…
    elsewhere = world.tmp / "otro_lado" / "v1" / "Acme"
    monkeypatch.setattr(assess, "governed_client_dir", lambda client: elsewhere)                                # …pero el almacén escribe a otro directorio, que no se persiste
    res, _ = pub(world)
    assert_aborted_untouched(world, res, "ASSESSMENT_NOT_PERSISTED")
    assert "directorio persistido" in res.message


def test_a_store_under_the_catalog_has_no_valid_directory_and_aborts(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.catalog / "governed")                                   # cae bajo el Catálogo: el servicio lo desactiva (None)
    res, _ = pub(world)
    assert_aborted_untouched(world, res, "ASSESSMENT_STORE_UNAVAILABLE")


# ═══════════════ la persistencia efectiva no confirma lo que se publica ═══════════════
def test_without_the_remote_confirmation_requested_a_successful_publication_would_not_be_durable_so_it_aborts(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.governed)
    res, _ = pub(world, mirror=False)
    assert_aborted_untouched(world, res, "PERSISTENCE_NOT_REQUESTED")


@pytest.mark.parametrize("mode", ["local", "read-only"])
def test_credentials_present_but_a_non_remote_persistence_mode_aborts(world, monkeypatch, mode):
    monkeypatch.setattr(config, "SPACES_CONFIGURED", True)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", mode)
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    res, _ = pub(world, mirror=False)
    assert_aborted_untouched(world, res, "PERSISTENCE_MODE_NOT_REMOTE")


def test_remote_mode_without_write_capability_aborts(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.governed)
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    res, _ = pub(world)
    assert_aborted_untouched(world, res, "PERSISTENCE_NOT_WRITABLE")


def test_a_catalog_outside_the_persisted_directories_aborts(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.governed)
    monkeypatch.setattr(config, "PERSIST_PATHS", [config.GOVERNED_ASSESSMENTS_NAME])
    res, _ = pub(world)
    assert_aborted_untouched(world, res, "CATALOG_NOT_PERSISTED")


# ═══════════════ cuando SÍ está cubierto, se publica y el assessment queda en Spaces ═══════════════
def test_a_covered_configuration_publishes_and_the_governed_assessment_is_confirmed_in_spaces(world, monkeypatch):
    enable_remote(monkeypatch, world.s3, governed=world.governed)
    res, _ = pub(world)
    assert (res.status, res.ok, res.durability) == (txn.COMPLETED, True, txn.DURABILITY_REMOTE)
    local = {p.relative_to(world.governed).as_posix(): p.read_bytes() for p in world.governed.rglob("*.json")}
    remote = {k[len("audit/governed-assessments/"):]: v for k, v in world.s3.objects.items() if k.startswith("audit/governed-assessments/")}
    assert local and local == remote                                                                            # el assessment no quedó solo en disco: Spaces tiene exactamente los mismos bytes


def test_a_local_development_environment_without_credentials_is_not_blocked(world):
    res, _ = pub(world, mirror=False)                                                                           # sin credenciales y modo local explícito: el disco es la fuente de verdad
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_NOT_REQUESTED
    assert any(world.governed.rglob("*.json"))


def test_a_publication_without_an_assessment_stage_is_not_subject_to_the_assessment_requirement(world, monkeypatch):
    enable_remote(monkeypatch, world.s3)                                                                        # sin 'governed-assessments' en las rutas…
    res, _ = world.publish(AM_A, mirror=True)                                                                   # …pero esta publicación no genera assessment
    assert res.status == txn.COMPLETED


# ═══════════════ cada causa tiene un mensaje para el usuario ═══════════════
def test_every_coverage_failure_has_a_user_facing_message():
    codes = {"ASSESSMENT_STORE_UNAVAILABLE", "PERSISTENCE_MODE_NOT_REMOTE", "PERSISTENCE_NOT_WRITABLE", "PERSISTENCE_NOT_REQUESTED", "CATALOG_NOT_PERSISTED", "ASSESSMENT_NOT_PERSISTED"}
    assert codes <= set(svc.PUBLICATION_MESSAGES)
    assert pr.persistence_gap(remote_store, mirror_up=True, catalog_root=__import__("pathlib").Path("."), durable_store=None) is None
