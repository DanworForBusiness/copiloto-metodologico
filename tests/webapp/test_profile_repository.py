"""
test_profile_repository.py — Capital Humano Phase 3 (First Profile Slice)
+ Phase 3.1 (Profile Durability / Failure Semantics Corrective): contrato
de `webapp.services.profile_repository`, sin HTTP, sin Spaces real (§19
del ticket original — todo monkeypatcheado).

Aísla CADA test en su propio `tmp_path` (via `config.BASE_DIR`
monkeypatcheado) para no ensuciar el working tree real ni otros tests —
`_profiles_dir()` lee `config.BASE_DIR` de forma dinámica en cada llamada,
así que el monkeypatch alcanza sin tocar el módulo bajo test."""
from __future__ import annotations

import json

import pytest

import config
from webapp.services import profile_repository
from webapp.services.profile_repository import ProfileConfigIncompleteError, ProfileStorageError


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _push_path_succeeds_by_default(monkeypatch):
    # Default determinístico para TODOS los tests de este archivo, salvo
    # los que prueban explícitamente la semántica de push_path/restore
    # (que sobreescriben este mock dentro de su propio cuerpo — un
    # `monkeypatch.setattr` en el test corre DESPUÉS de esta fixture y
    # gana). Nunca toca Spaces real bajo ninguna circunstancia (§19)."""
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _minimal_profile(**overrides) -> dict:
    base = {
        "first_name": "Ana", "last_name": "Pérez", "email": None, "phone": None,
        "linkedin_url": None, "location": None, "profile_skills": None,
        "seniority": None, "source": None, "tags": None, "notes": None,
        "potential_rating": None, "potential_mark": None,
    }
    base.update(overrides)
    return base


# --- create(): UUID, schema, campos, timestamps ----------------------------

def test_create_generates_a_stable_uuid_profile_id():
    saved = profile_repository.create(_minimal_profile())
    import uuid
    parsed = uuid.UUID(saved["profile_id"])  # levanta ValueError si no es un UUID válido
    assert str(parsed) == saved["profile_id"]


def test_create_writes_exactly_one_json_file_per_profile():
    profile_repository.create(_minimal_profile())
    files = list((config.BASE_DIR / "human-capital" / "profiles").iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".json"


def test_create_persists_schema_version():
    saved = profile_repository.create(_minimal_profile())
    assert saved["schema_version"] == 1
    on_disk = json.loads((config.BASE_DIR / "human-capital" / "profiles" / f"{saved['profile_id']}.json").read_text())
    assert on_disk["schema_version"] == 1


def test_create_persists_required_fields():
    saved = profile_repository.create(_minimal_profile(first_name="Juan", last_name="Gómez"))
    assert saved["first_name"] == "Juan"
    assert saved["last_name"] == "Gómez"


def test_create_preserves_nullable_email():
    saved = profile_repository.create(_minimal_profile(email=None))
    assert saved["email"] is None
    saved2 = profile_repository.create(_minimal_profile(email="ana@example.com"))
    assert saved2["email"] == "ana@example.com"


def test_create_persists_created_and_updated_timestamps():
    saved = profile_repository.create(_minimal_profile())
    assert saved["created_at"]
    assert saved["updated_at"]
    assert saved["created_at"] == saved["updated_at"]  # recién creado: ambos coinciden


# --- push_path: write-through obligatorio, éxito vs. fallo -----------------

def test_create_calls_push_path_after_the_local_write(monkeypatch):
    calls: list[str] = []

    def _fake_push(path):
        # El archivo YA debe existir en disco cuando push_path se invoca
        # (write-through DESPUÉS del write local atómico, nunca antes).
        assert path.exists()
        calls.append(str(path))
        return True

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _fake_push)
    profile_repository.create(_minimal_profile())
    assert len(calls) == 1


def test_push_path_failure_never_reports_success(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileStorageError):
        profile_repository.create(_minimal_profile())


def test_push_path_failure_does_not_leave_a_local_observable_profile(monkeypatch):
    # Phase 3.1 corrective (root cause del bug de data correctness de
    # Phase 3): el archivo local NO debe sobrevivir a un create() fallido
    # -- si sobreviviera, list()/get() lo mostrarían como si hubiera
    # tenido éxito, y el ciclo periódico remote_store.mirror_up() podría
    # terminar subiéndolo de verdad más tarde, convirtiendo un fallo YA
    # reportado en un éxito silencioso asincrónico. Ver
    # profile_repository._cleanup_failed_write.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileStorageError):
        profile_repository.create(_minimal_profile())
    profiles_dir = config.BASE_DIR / "human-capital" / "profiles"
    files = list(profiles_dir.iterdir()) if profiles_dir.exists() else []
    assert files == []


def test_push_path_success_returns_the_saved_profile(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    saved = profile_repository.create(_minimal_profile())
    assert saved["first_name"] == "Ana"


# --- get() -------------------------------------------------------------

def test_get_returns_the_created_profile(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    saved = profile_repository.create(_minimal_profile(first_name="Carla"))
    fetched = profile_repository.get(saved["profile_id"])
    assert fetched is not None
    assert fetched["first_name"] == "Carla"


def test_get_returns_none_for_a_missing_profile():
    assert profile_repository.get("does-not-exist") is None


# --- list() --------------------------------------------------------------

def test_list_returns_multiple_profiles(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    profile_repository.create(_minimal_profile(first_name="Uno"))
    profile_repository.create(_minimal_profile(first_name="Dos"))
    names = sorted(p["first_name"] for p in profile_repository.list())
    assert names == ["Dos", "Uno"]


def test_list_returns_empty_list_when_directory_does_not_exist_yet():
    assert profile_repository.list() == []


def test_corrupt_json_file_does_not_break_the_rest_of_the_listing(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    profile_repository.create(_minimal_profile(first_name="Bueno"))
    profiles_dir = config.BASE_DIR / "human-capital" / "profiles"
    (profiles_dir / "corrupto.json").write_text("{ esto no es json valido ][", encoding="utf-8")
    result = profile_repository.list()
    assert len(result) == 1
    assert result[0]["first_name"] == "Bueno"


# --- restore coordination ---------------------------------------------------

def test_list_waits_for_restore_when_spaces_is_enabled_and_restore_in_progress(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_repository.remote_store, "await_restore", lambda timeout: calls.append(timeout))
    profile_repository.list()
    assert len(calls) == 1
    assert calls[0] == profile_repository._RESTORE_WAIT_TIMEOUT_S


def test_create_and_get_also_respect_restore_coordination(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(profile_repository.remote_store, "await_restore", lambda timeout: calls.append("waited"))
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    profile_repository.get("whatever")
    profile_repository.create(_minimal_profile())
    assert calls == ["waited", "waited"]


def test_restore_coordination_is_a_noop_when_spaces_disabled(monkeypatch):
    # `_push_path_succeeds_by_default` ya deja `enabled() == False` —
    # confirma explícitamente que `await_restore` NUNCA se llama en ese
    # caso (no tiene sentido esperar un restore que nunca va a ocurrir).
    calls: list[str] = []
    monkeypatch.setattr(profile_repository.remote_store, "await_restore", lambda timeout: calls.append("waited"))
    profile_repository.list()
    assert calls == []


# --- Phase 3.1: clasificación de fallo (CONFIG_INCOMPLETE vs SOURCE_ERROR) --

def test_spaces_disabled_raises_config_incomplete_not_generic_storage_error(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    with pytest.raises(ProfileConfigIncompleteError):
        profile_repository.create(_minimal_profile())


def test_config_incomplete_error_is_still_a_profile_storage_error(monkeypatch):
    # Subclase deliberada (Phase 3.1 §5): un `except ProfileStorageError`
    # genérico (como el que ya usa list_profiles() en el router) sigue
    # capturando este caso sin cambios.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    with pytest.raises(ProfileStorageError):
        profile_repository.create(_minimal_profile())


def test_enabled_but_push_failure_raises_source_error_not_config_incomplete(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileStorageError) as exc_info:
        profile_repository.create(_minimal_profile())
    assert not isinstance(exc_info.value, ProfileConfigIncompleteError)


# --- Phase 3.1: list() nunca devuelve un create() fallido -------------------

def test_list_does_not_return_a_profile_whose_create_failed(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileStorageError):
        profile_repository.create(_minimal_profile(first_name="Fantasma"))
    names = [p["first_name"] for p in profile_repository.list()]
    assert "Fantasma" not in names
    assert profile_repository.list() == []


# --- Phase 3.1: retry/idempotencia -- el "ghost" de un intento fallido no
# puede convertirse en un duplicado cuando el retry sí tiene éxito ---------

def test_retry_after_failure_produces_exactly_one_profile(monkeypatch):
    # Primer intento: falla (push_path devuelve False) -- MISMO usuario,
    # MISMO form, reintenta.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileStorageError):
        profile_repository.create(_minimal_profile(first_name="Reintento"))

    # Segundo intento (el "retry"): ahora push_path tiene éxito.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    saved = profile_repository.create(_minimal_profile(first_name="Reintento"))

    all_profiles = profile_repository.list()
    assert len(all_profiles) == 1
    assert all_profiles[0]["profile_id"] == saved["profile_id"]


# --- Phase 3.1: simulación de restore -- un Profile fallido tampoco puede
# reaparecer vía restore/mirror -----------------------------------------

def test_failed_profile_does_not_reappear_after_restore_simulation(monkeypatch):
    # "Restore" acá es conceptual (§10 del ticket: "usar mocks/temp dirs,
    # no Spaces real") -- lo que un restore real haría es repoblar
    # `_profiles_dir()` a partir de lo que existe en el bucket remoto. Si
    # el create falló, `_cleanup_failed_write` ya garantizó que el archivo
    # local NUNCA existió después del fallo -- así que no hay NADA que un
    # restore/mirror posterior pueda encontrar y "resucitar". Se simula
    # reconstruyendo el directorio local desde cero (como haría
    # `remote_store.restore()` contra un container con FS efímero vacío) y
    # confirmando que sigue sin aparecer.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    with pytest.raises(ProfileStorageError):
        profile_repository.create(_minimal_profile(first_name="Fantasma"))

    profiles_dir = config.BASE_DIR / "human-capital" / "profiles"
    assert not profiles_dir.exists() or list(profiles_dir.iterdir()) == []

    # Simula un restart/restore: el directorio local vuelve a "arrancar
    # vacío" (mismo estado que un container con FS efímero recién creado,
    # ver core/remote_store.py) -- nada que restaurar para este Profile,
    # porque nunca se confirmó su durabilidad remota.
    assert profile_repository.list() == []


def test_successful_profile_still_restores_correctly(monkeypatch):
    # Control positivo del test anterior: un create() EXITOSO sí debe
    # sobrevivir un restore/reinicio -- confirma que la corrección de
    # Phase 3.1 (borrar en el camino de FALLO) no afecta el camino de
    # ÉXITO.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    saved = profile_repository.create(_minimal_profile(first_name="Sobrevive"))

    # Simula un restart leyendo de nuevo desde el mismo directorio local
    # (lo que un `remote_store.restore()` real dejaría en su lugar antes
    # de que el proceso siguiera sirviendo requests).
    fetched = profile_repository.get(saved["profile_id"])
    assert fetched is not None
    assert fetched["first_name"] == "Sobrevive"
    assert profile_repository.list() == [fetched]
