"""Segundo fallo del CLI `fix_contador_sr_profiles` en un proceso independiente: el catálogo gobernado de CCHH (`human-capital/catalogs/catalogos.json`) también lo restaura solo el lifespan de la app web, y el CLI lo validaba
(`Senior`) ANTES de restaurar nada -> «SENIORITY_NOT_IN_GOVERNED_CATALOG · catálogo no disponible». Ahora el CLI restaura el catálogo con el mismo `remote_store.restore()` (acotado, sin sobrescribir) y distingue «catálogo no
disponible» de «valor inexistente». Spaces simulado con las funciones reales de `remote_store`; disco vacío; datos sintéticos."""
from __future__ import annotations

import json
from pathlib import Path

import config
from core import remote_store
from tools.human_capital import fix_contador_sr_profiles as tool
from webapp.services import human_capital_catalog_repository as catalog_repo, profile_repository as repo
from tests.test_cchh_fix_contador_sr_standalone import FakeS3, _doc, _run, _seed_remote, s3  # noqa: F401  (fixture del proceso independiente)

CATALOG_KEY = "human-capital/catalogs/catalogos.json"


def _snapshot_bytes(**cats) -> bytes:
    snap = catalog_repo.build_snapshot({n: [{"valor": v, "orden": i + 1} for i, v in enumerate(vals)] for n, vals in cats.items()}, refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    return catalog_repo._serialize(snap)


def _empty_disk_with_remote_catalog(fake, **cats):
    """El proceso independiente: el `s3` del test anterior sembró un catálogo local; acá el disco NO lo tiene, solo Spaces."""
    catalog_repo.snapshot_path().unlink()
    if cats:
        fake.objects[CATALOG_KEY] = _snapshot_bytes(**cats)


def test_the_symptom_a_standalone_process_cannot_see_the_catalog_that_exists_in_spaces(s3):
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "Senior"])
    assert catalog_repo.catalog_values("seniority") == [] and catalog_repo.snapshot_meta() is None           # lo que validaba el CLI antes de restaurar


def test_the_cli_restores_the_governed_catalog_before_validating_senior(s3):
    ids = _seed_remote(s3)
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "Senior"])
    code, text = _run()
    assert code == tool.EXIT_OK and "SENIORITY_NOT_IN_GOVERNED_CATALOG" not in text and "CATALOG_NOT_AVAILABLE" not in text
    assert "Catálogos en Spaces: 1 · locales antes: 0 · descargados: 1" in text and "Perfiles en Spaces: 6 · locales antes: 0 · descargados: 6" in text
    assert "a_modificar: 2" in text and s3.uploads == []                                                     # dry-run: solo lecturas
    assert catalog_repo.catalog_values("seniority") == ["Junior", "Semi Senior", "Senior"] and config.PERSIST_PATHS[:3] == ["catalog_local", "case_library", "human-capital"]
    assert {ids["Ana"], ids["Beto"]} <= {p["profile_id"] for p in repo.list()}


def test_execute_from_an_empty_disk_validates_against_the_restored_catalog_and_updates_only_through_the_service(s3):
    ids = _seed_remote(s3)
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "Senior"])
    phrase = _run()[1].split("--confirm ")[1].split()[0]
    code, text = _run("--execute", "--confirm", phrase)
    assert code == tool.EXIT_OK and "modificado: 2" in text
    assert json.loads(s3.objects[f"human-capital/profiles/{ids['Ana']}.json"])["seniority"] == "Senior"
    assert sorted(s3.uploads) == sorted(f"human-capital/profiles/{ids[n]}.json" for n in ("Ana", "Beto"))      # solo los 2 perfiles; ningún valor de catálogo creado ni catálogo subido
    assert CATALOG_KEY not in s3.uploads                                                                     # el catálogo gobernado no se escribe


def test_a_catalog_missing_everywhere_is_reported_as_unavailable_not_as_a_missing_value(s3):
    _seed_remote(s3)
    _empty_disk_with_remote_catalog(s3)                                                                      # ni en disco ni en Spaces
    code, text = _run()
    assert code == tool.EXIT_BLOCKED and "CATALOG_NOT_AVAILABLE" in text and "SENIORITY_NOT_IN_GOVERNED_CATALOG" not in text and "No se creó ningún valor" in text
    assert s3.uploads == [] and not (Path(config.BASE_DIR) / "human-capital" / "profiles").exists()         # no se leyó ni restauró ningún perfil


def test_a_snapshot_without_the_seniority_catalog_is_also_unavailable(s3):
    _empty_disk_with_remote_catalog(s3, fuente=["LinkedIn"])
    code, text = _run()
    assert code == tool.EXIT_BLOCKED and "CATALOG_NOT_AVAILABLE" in text and s3.uploads == []


def test_an_existing_catalog_without_the_exact_value_is_a_real_missing_value_and_lists_the_current_ones(s3):
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "SR"])
    remote_before = s3.objects[CATALOG_KEY]
    for args in ((), ("--execute", "--confirm", "x")):
        code, text = _run(*args)
        assert code == tool.EXIT_BLOCKED and "SENIORITY_NOT_IN_GOVERNED_CATALOG" in text and "CATALOG_NOT_AVAILABLE" not in text and "Junior, Semi Senior, SR" in text
    assert s3.uploads == [] and s3.objects[CATALOG_KEY] == remote_before                                    # nunca se inventa el valor
    assert _run("--seniority", "SR")[0] == tool.EXIT_OK                                                      # otro valor exacto vigente sí pasa


def test_a_local_catalog_that_diverges_from_spaces_blocks_and_is_never_overwritten(s3):
    _seed_remote(s3)
    local = catalog_repo.snapshot_path()
    s3.objects[CATALOG_KEY] = _snapshot_bytes(seniority=["Junior", "Semi Senior", "Senior"])
    local_before = local.read_bytes()                                                                        # el catálogo local sembrado difiere del remoto
    assert local_before != s3.objects[CATALOG_KEY]
    code, text = _run("--execute", "--confirm", "x")
    assert code == tool.EXIT_BLOCKED and "RESTORE_LOCAL_DISTINTO_DEL_REMOTO" in text and "Catálogos en Spaces" in text and s3.uploads == []
    assert local.read_bytes() == local_before                                                                # preservado, no pisado
    assert not (Path(config.BASE_DIR) / "human-capital" / "profiles").exists()


def test_a_failed_catalog_download_blocks_before_any_profile_is_read(s3):
    _seed_remote(s3)
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "Senior"])
    s3.fail_download = {CATALOG_KEY}
    code, text = _run("--execute", "--confirm", "x")
    assert code == tool.EXIT_BLOCKED and "RESTORE_INCOMPLETO" in text and s3.uploads == [] and not (Path(config.BASE_DIR) / "human-capital" / "profiles").exists()


def test_a_failed_catalog_listing_blocks(s3):
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "Senior"])
    s3.listing_error = True
    code, text = _run()
    assert code == tool.EXIT_BLOCKED and "RESTORE_LISTADO_REMOTO_FALLIDO" in text and s3.uploads == []


def test_the_restore_is_scoped_to_catalogs_and_profiles_only(s3):
    _seed_remote(s3)
    _empty_disk_with_remote_catalog(s3, seniority=["Junior", "Semi Senior", "Senior"])
    s3.objects["human-capital/entrevistas/e1.json"] = b"{}"
    s3.objects["human-capital/profile-documents/d1.json"] = b"{}"
    s3.objects["reports/informe.json"] = b"{}"
    assert _run()[0] == tool.EXIT_OK
    base = Path(config.BASE_DIR)
    assert (base / "human-capital" / "catalogs" / "catalogos.json").exists() and (base / "human-capital" / "profiles").exists()
    assert not (base / "human-capital" / "entrevistas").exists() and not (base / "human-capital" / "profile-documents").exists() and not (base / "reports").exists()
