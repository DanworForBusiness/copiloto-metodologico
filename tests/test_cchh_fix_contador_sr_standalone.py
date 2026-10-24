"""El CLI `fix_contador_sr_profiles` corre en un proceso APARTE de la app web. `profile_repository.list()` lee solo el disco local y el único `remote_store.restore()` que lo puebla corre en el lifespan de la app web: sin restaurar,
un proceso independiente ve 0 perfiles aunque Spaces tenga cientos (el dry-run de producción dio 0). Estos tests usan las funciones REALES de `remote_store` (restore, inventario, push_path) contra un cliente S3 simulado y un disco vacío.
Datos sintéticos; nada contra DigitalOcean."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
from core import remote_store
from tools.human_capital import fix_contador_sr_profiles as tool
from webapp.services import profile_repository as repo
from tests.webapp.test_busqueda_solicitud_i1 import seed_catalog

BUCKET = "bucket-sintetico"


class FakeS3:
    """Subconjunto del cliente S3 que usan `restore`, `list_persisted_objects` y `push_path`."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.listing_error = False
        self.fail_download: set[str] = set()

    def get_paginator(self, _name):
        return self

    def paginate(self, Bucket, Prefix):                                                  # noqa: N803 — firma de boto3
        if self.listing_error:
            raise RuntimeError("listado caído")
        contents = [{"Key": k, "Size": len(v), "ETag": '"' + hashlib.md5(v).hexdigest() + '"', "LastModified": datetime(2026, 10, 24, tzinfo=timezone.utc)}
                    for k, v in sorted(self.objects.items()) if k.startswith(Prefix)]
        yield {"Contents": contents}

    def download_file(self, bucket, key, dest):
        if key in self.fail_download:
            raise RuntimeError("descarga caída")
        Path(dest).write_bytes(self.objects[key])

    def upload_file(self, src, bucket, key):
        self.uploads.append(key)
        self.objects[key] = Path(src).read_bytes()


def _doc(pid: str, first: str, skills: str, seniority=None, revision=1) -> bytes:
    return json.dumps({"schema_version": 1, "profile_id": pid, "revision": revision, "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
                       "first_name": first, "last_name": "Sintetico", "email": f"{first.lower()}@sintetico.test", "profile_skills": skills, "seniority": seniority, "tags": "historico",
                       "notes": "nota", "potential_rating": 3, "import_origin": {"kind": "sharepoint_historical", "run_id": "r1"}}, ensure_ascii=False).encode("utf-8")


@pytest.fixture()
def s3(tmp_path, monkeypatch):
    """Proceso INDEPENDIENTE: disco vacío, modo remote con credenciales, sin arranque de la app web (nadie llamó a `restore()`)."""
    fake = FakeS3()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", False)
    monkeypatch.setattr(config, "SPACES_CONFIGURED", True)
    monkeypatch.setattr(config, "SPACES_BUCKET", BUCKET)
    monkeypatch.setattr(config, "SPACES_PREFIX", "")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local", "case_library", "human-capital", "reports", "governed-assessments"])
    monkeypatch.setattr(remote_store, "_get_client", lambda: fake)
    monkeypatch.setattr(remote_store, "_restore_state", "not_applicable")
    remote_store._restore_done.clear()
    remote_store._unreconciled.clear()
    remote_store._signatures.clear()
    remote_store.last_restore_report.clear()
    seed_catalog(seniority=["Junior", "Semi Senior", "Senior"])
    return fake


def _seed_remote(fake, prefix="", n_plain=3):
    ids = {}
    base = f"{prefix}/" if prefix else ""
    for i, (first, skills, sen) in enumerate([("Ana", "Contador SR", None), ("Beto", "Auditoría, Contador SR", None), ("Carla", "Contador SR", "Junior")] + [(f"P{k}", "Impuestos", None) for k in range(n_plain)]):
        pid = f"00000000-0000-4000-8000-{i:012d}"
        fake.objects[f"{base}human-capital/profiles/{pid}.json"] = _doc(pid, first, skills, sen)
        ids[first] = pid
    return ids


def _run(*argv):
    out = io.StringIO()
    code = tool.run(list(argv), out=out)
    return code, out.getvalue()


def test_without_restoring_a_standalone_process_sees_no_profiles_which_is_the_production_symptom(s3):
    _seed_remote(s3)
    assert repo.list() == []                                                           # disco local vacío: lo que veía el CLI antes de la corrección
    assert remote_store.restore_status() == "not_applicable"


def test_the_standalone_cli_reads_the_existing_remote_profiles_through_the_existing_restore(s3):
    ids = _seed_remote(s3)
    code, text = _run()
    assert code == tool.EXIT_OK
    assert "Perfiles en Spaces: 6 · locales antes: 0 · descargados: 6" in text
    assert "perfiles_leidos: 6" in text and "a_modificar: 2" in text and "conflictos_seniority: 1" in text and "ATENCIÓN: 0 perfiles leídos" not in text
    assert s3.uploads == []                                                            # el dry-run solo LEE del bucket
    assert len(repo.list()) == 6 and config.PERSIST_PATHS == ["catalog_local", "case_library", "human-capital", "reports", "governed-assessments"]     # el acotado a perfiles no queda activo
    assert {ids["Ana"], ids["Beto"]} <= {p["profile_id"] for p in repo.list()}


def test_execute_from_a_standalone_process_updates_the_remote_object_under_the_same_key_and_preserves_the_rest(s3):
    ids = _seed_remote(s3)
    phrase = _run()[1].split("--confirm ")[1].split()[0]
    code, text = _run("--execute", "--confirm", phrase)
    assert code == tool.EXIT_OK and "modificado: 2" in text
    key = f"human-capital/profiles/{ids['Beto']}.json"
    assert s3.uploads.count(key) == 1 and sorted(s3.uploads) == sorted(f"human-capital/profiles/{ids[n]}.json" for n in ("Ana", "Beto"))      # solo los 2 modificados, misma clave que usa el Hub
    remote = json.loads(s3.objects[key])
    assert remote["profile_skills"] == "Auditoría, Contador" and remote["seniority"] == "Senior" and remote["revision"] == 2
    assert (remote["tags"], remote["notes"], remote["potential_rating"], remote["import_origin"]) == ("historico", "nota", 3, {"kind": "sharepoint_historical", "run_id": "r1"})
    assert json.loads(s3.objects[f"human-capital/profiles/{ids['Carla']}.json"])["seniority"] == "Junior"      # conflicto: intacto
    before = dict(s3.objects)
    code, text = _run("--execute", "--confirm", "x")                                   # idempotente: segunda corrida en proceso nuevo
    assert code == tool.EXIT_OK and "Nada para modificar" in text and s3.objects == before


def test_it_reads_and_writes_only_inside_the_configured_prefix(s3, monkeypatch):
    monkeypatch.setattr(config, "SPACES_PREFIX", "prod")
    prod = _seed_remote(s3, prefix="prod")
    s3.objects["staging/human-capital/profiles/zzzz.json"] = _doc("zzzz", "Zeta", "Contador SR")      # otro ambiente: no se lee
    code, text = _run()
    assert code == tool.EXIT_OK and "perfiles_leidos: 6" in text
    phrase = text.split("--confirm ")[1].split()[0]
    _run("--execute", "--confirm", phrase)
    assert all(k.startswith("prod/human-capital/profiles/") for k in s3.uploads) and f"prod/human-capital/profiles/{prod['Ana']}.json" in s3.uploads
    assert json.loads(s3.objects["staging/human-capital/profiles/zzzz.json"])["profile_skills"] == "Contador SR"


def test_a_failed_remote_listing_blocks_without_reading_or_writing(s3):
    _seed_remote(s3)
    s3.listing_error = True
    code, text = _run()
    assert code == tool.EXIT_BLOCKED and "RESTORE_LISTADO_REMOTO_FALLIDO" in text and s3.uploads == [] and repo.list() == []


def test_an_incomplete_restore_blocks_instead_of_working_on_partial_data(s3):
    ids = _seed_remote(s3)
    s3.fail_download = {f"human-capital/profiles/{ids['Beto']}.json"}
    code, text = _run("--execute", "--confirm", "x")
    assert code == tool.EXIT_BLOCKED and "RESTORE_INCOMPLETO" in text and "fallidos: 1" in text and s3.uploads == []


def test_a_local_copy_that_differs_from_the_remote_blocks_instead_of_overwriting_newer_data(s3):
    ids = _seed_remote(s3)
    local = Path(config.BASE_DIR) / "human-capital" / "profiles"
    local.mkdir(parents=True)
    (local / f"{ids['Ana']}.json").write_bytes(_doc(ids["Ana"], "Ana", "Contador SR", revision=7))      # copia local obsoleta/distinta
    code, text = _run("--execute", "--confirm", "x")
    assert code == tool.EXIT_BLOCKED and "RESTORE_LOCAL_DISTINTO_DEL_REMOTO" in text and "locales distintos del remoto: 1" in text and s3.uploads == []


def test_in_local_mode_the_disk_is_the_source_and_nothing_is_restored(s3, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(config, "SPACES_CONFIGURED", False)
    monkeypatch.setattr(remote_store, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("no debe tocar Spaces")))
    repo.create({"first_name": "Local", "last_name": "Test", "profile_skills": "Contador SR"})
    code, text = _run()
    assert code == tool.EXIT_OK and "perfiles_leidos: 1" in text and "Perfiles en Spaces" not in text
