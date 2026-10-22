"""
test_profile_repository.py — Capital Humano Phase 3 (First Profile Slice)
+ Phase 3.1 (Profile Durability / Failure Semantics Corrective) + Phase
3.5 (Safe Profile Edit — Revision + Compensating Overwrite): contrato de
`webapp.services.profile_repository`, sin HTTP, sin Spaces real (§19 del
ticket original — todo monkeypatcheado).

Aísla CADA test en su propio `tmp_path` (via `config.BASE_DIR`
monkeypatcheado) para no ensuciar el working tree real ni otros tests —
`_profiles_dir()` lee `config.BASE_DIR` de forma dinámica en cada llamada,
así que el monkeypatch alcanza sin tocar el módulo bajo test."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import config
from webapp.services import profile_repository
from webapp.services.profile_repository import (
    ProfileConcurrentModificationError,
    ProfileConfigIncompleteError,
    ProfileIdConflictError,
    ProfileNotFoundError,
    ProfileRemoteOutcomeUnknownError,
    ProfileStorageError,
)


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
    # Este archivo prueba el contrato REMOTE (push obligatorio, compensación, clasificación de fallo): el modo es explícito. Los modos local/read-only se prueban en
    # `test_profile_repository_persistence_modes.py`.
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")


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


# ============================================================================
# Phase 3.6 — CREATE in-doubt remote durability: same-key retry + honest
# UNKNOWN classification (§19 del ticket)
# ============================================================================

def test_confirmed_success_calls_push_path_exactly_once(monkeypatch):
    # §19 "confirmed normal success regression": el camino feliz NUNCA
    # reintenta -- solo un push cuando el primero ya confirmó.
    calls: list = []
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: calls.append(path) or True)
    profile_repository.create(_minimal_profile())
    assert len(calls) == 1


def test_initial_failure_then_retry_success_produces_exactly_one_profile(monkeypatch):
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return calls["n"] > 1  # falla la 1ra vez, confirma la 2da (el reintento)

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)

    saved = profile_repository.create(_minimal_profile(first_name="Reintentado"))

    assert calls["n"] == 2  # 1 intento inicial + 1 reintento explícito, nunca más
    all_profiles = profile_repository.list()
    assert len(all_profiles) == 1
    assert all_profiles[0]["profile_id"] == saved["profile_id"]


def test_retry_never_mints_a_second_uuid(monkeypatch):
    seen_paths: list = []
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        seen_paths.append(path)
        return calls["n"] > 1

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)

    profile_repository.create(_minimal_profile())

    assert len(seen_paths) == 2
    assert seen_paths[0] == seen_paths[1]  # MISMO path/profile_id en ambos intentos


def test_initial_failure_and_retry_failure_raises_remote_outcome_unknown(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.create(_minimal_profile())


def test_config_incomplete_does_not_attempt_a_pointless_retry(monkeypatch):
    # §19 "config_incomplete -> no pointless retry": con enabled()==False,
    # push_path() ya devuelve False determinísticamente en cada llamada --
    # reintentar no puede cambiar nada, así que NUNCA se llama una 2da vez.
    calls: list = []

    def _push(path):
        calls.append(path)
        return False

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    with pytest.raises(ProfileConfigIncompleteError):
        profile_repository.create(_minimal_profile())
    assert len(calls) == 1


def test_unknown_create_does_not_appear_in_list(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.create(_minimal_profile(first_name="Fantasma"))
    assert profile_repository.list() == []


def test_unknown_create_leaves_no_local_file(monkeypatch):
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.create(_minimal_profile())
    profiles_dir = config.BASE_DIR / "human-capital" / "profiles"
    files = list(profiles_dir.iterdir()) if profiles_dir.exists() else []
    assert files == []


def test_unknown_create_cannot_be_silently_exposed_by_a_simulated_restore(monkeypatch):
    # §19 "unknown create -> simulated restore cannot silently expose it":
    # el archivo LOCAL nunca sobrevivió (ver test anterior) -- un restore
    # simulado (releer desde el mismo directorio local, como haría
    # remote_store.restore() sobre un FS efímero recién arrancado) no
    # tiene NADA que pueda "resucitar" localmente. El riesgo residual de
    # un objeto REMOTO huérfano está divulgado (ver docstring del módulo)
    # pero no es lo que este test prueba -- esto prueba el lado LOCAL,
    # que es el único que profile_repository controla directamente.
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.create(_minimal_profile(first_name="Fantasma"))

    profiles_dir = config.BASE_DIR / "human-capital" / "profiles"
    assert not profiles_dir.exists() or list(profiles_dir.iterdir()) == []
    # Simula un restart/restore: el directorio local vuelve a "arrancar
    # vacío" -- nada que restaurar LOCALMENTE para este intento.
    assert profile_repository.list() == []


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


def test_enabled_but_push_failure_after_retry_raises_remote_outcome_unknown_not_config_incomplete(monkeypatch):
    # Phase 3.6 reclasificación (ver docstring del módulo, "CREATE -- IN-
    # DOUBT REMOTE DURABILITY"): con Spaces configurado, un push inicial
    # fallido YA NO se reporta como un ProfileStorageError/SOURCE_ERROR
    # común tras el reintento explícito de Phase 3.6 -- un `push_path()`
    # que falla dos veces seguidas no PRUEBA ausencia remota (§7/§10 del
    # ticket original de esta fase), así que la clasificación honesta es
    # ProfileRemoteOutcomeUnknownError, nunca un source_error que
    # afirmaría más certeza de la que existe. Sigue, de todos modos,
    # NUNCA siendo ConfigIncompleteError (eso solo aplica con Spaces
    # deshabilitado).
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileRemoteOutcomeUnknownError) as exc_info:
        profile_repository.create(_minimal_profile())
    assert not isinstance(exc_info.value, ProfileConfigIncompleteError)
    assert not isinstance(exc_info.value, ProfileStorageError)


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


def test_list_and_filter_performance_baseline_local_benchmark():
    # Phase 3.2 §13/§14 — SOLO evidencia para un baseline, NUNCA un gate de
    # producción ("no inventar un límite de producción a partir de un
    # benchmark sintético", §13). Crea un dataset local controlado,
    # cronometra `repository.list()` y `human_capital_service.
    # filter_profiles()` POR SEPARADO (son dos costos distintos: leer todo
    # de disco vs. filtrar en memoria lo ya leído — ver Phase 3.2 §14, sin
    # índice/cache en ninguno de los dos). Los números concretos
    # observados en una corrida local se reportan en el OUTPUT de la fase
    # (§30 "Performance baseline"), no se hardcodean acá. Los `assert
    # ... < 5.0` son un umbral deliberadamente laxo (atrapar solo una
    # regresión catastrófica, p. ej. un loop infinito o una lectura
    # O(n²)) — nunca un SLA de producción inventado desde un benchmark
    # sintético.
    import time

    from webapp.services import human_capital_service

    profile_count = 300
    for i in range(profile_count):
        profile_repository.create(_minimal_profile(
            first_name=f"Persona{i}", last_name="Test",
            seniority="Senior" if i % 2 == 0 else "Junior",
        ))

    start = time.perf_counter()
    profiles = profile_repository.list()
    list_duration = time.perf_counter() - start
    assert len(profiles) == profile_count

    start = time.perf_counter()
    filtered = human_capital_service.filter_profiles(profiles, {"q": "persona1", "seniority": "Senior"})
    filter_duration = time.perf_counter() - start
    assert filtered  # "Persona1", "Persona10".."Persona19", etc. con seniority Senior

    print(
        f"\n[Phase 3.2 perf baseline] N={profile_count} "
        f"list()={list_duration * 1000:.2f}ms filter()={filter_duration * 1000:.2f}ms"
    )
    assert list_duration < 5.0
    assert filter_duration < 5.0


# --- Phase 3.3: contrato de remote_store.push_path() ejercido de punta a
# punta a través de create() (no mockeado como caja negra) --------------

def test_create_succeeds_end_to_end_when_only_signature_bookkeeping_fails(monkeypatch):
    # Phase 3.3 §11/§10: a diferencia de todos los demás tests de este
    # archivo, acá NO se mockea `profile_repository.remote_store.
    # push_path` directamente -- se ejerce su implementación REAL (con
    # `_get_client`/`_sig` mockeados en el punto de inyección más bajo que
    # `remote_store.py` ya expone) a través de `create()`, para probar
    # extremo a extremo que un PUT remoto exitoso con bookkeeping de firma
    # fallido produce una creación EXITOSA -- sin ProfileConfigIncompleteError/
    # ProfileStorageError, sin cleanup, el Profile queda en list().
    from core import remote_store as remote_store_module

    class _FakeClient:
        def upload_file(self, filename, bucket, key):
            pass  # el PUT remoto "sube" sin error

    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(remote_store_module, "_get_client", lambda: _FakeClient())

    def _boom_sig(_path):
        raise OSError("simulated post-upload bookkeeping failure")
    monkeypatch.setattr(remote_store_module, "_sig", _boom_sig)

    saved = profile_repository.create(_minimal_profile(first_name="SoloBookkeepingFalla"))

    assert saved["first_name"] == "SoloBookkeepingFalla"
    all_profiles = profile_repository.list()
    assert len(all_profiles) == 1
    assert all_profiles[0]["profile_id"] == saved["profile_id"]


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


# ============================================================================
# Phase 3.5 — revision (§19 del ticket)
# ============================================================================

def test_create_sets_revision_1():
    saved = profile_repository.create(_minimal_profile())
    assert saved["revision"] == 1


def test_legacy_profile_missing_revision_reads_as_1():
    # Simula un Profile persistido en Phase 3/3.1/3.2/3.3, ANTES de que
    # `revision` existiera -- escribe el JSON a mano, sin ese campo.
    saved = profile_repository.create(_minimal_profile(first_name="Legacy"))
    path = profile_repository._profile_path(saved["profile_id"])
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    del on_disk["revision"]
    path.write_text(json.dumps(on_disk), encoding="utf-8")

    fetched = profile_repository.get(saved["profile_id"])
    assert fetched["revision"] == 1
    listed = [p for p in profile_repository.list() if p["profile_id"] == saved["profile_id"]][0]
    assert listed["revision"] == 1


def test_read_does_not_rewrite_the_legacy_file():
    # §6 del ticket: "Do not silently rewrite Profiles merely because
    # they were read" -- el archivo en disco debe seguir SIN `revision`
    # después de un get()/list(), solo el dict en memoria la expone.
    saved = profile_repository.create(_minimal_profile(first_name="Legacy"))
    path = profile_repository._profile_path(saved["profile_id"])
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    del on_disk["revision"]
    path.write_text(json.dumps(on_disk), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    profile_repository.get(saved["profile_id"])
    profile_repository.list()

    after = path.read_text(encoding="utf-8")
    assert after == before
    assert "revision" not in json.loads(after)


def test_successful_update_increments_revision_from_1_to_2():
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    updated = profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Ana2"))
    assert updated["revision"] == 2
    assert updated["first_name"] == "Ana2"


def test_second_update_increments_revision_from_2_to_3():
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Ana2"))
    updated_again = profile_repository.update(saved["profile_id"], 2, _minimal_profile(first_name="Ana3"))
    assert updated_again["revision"] == 3


def test_failed_update_preserves_the_previous_revision(monkeypatch):
    # B falla (1ra llamada a push_path), la compensación A confirma (2da)
    # -- SOURCE_ERROR con rollback exitoso, no remote_outcome_unknown.
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileStorageError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Ana2"))
    current = profile_repository.get(saved["profile_id"])
    assert current["revision"] == 1
    assert current["first_name"] == "Ana"


def test_update_preserves_created_at():
    saved = profile_repository.create(_minimal_profile())
    updated = profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Cambiado"))
    assert updated["created_at"] == saved["created_at"]


def test_update_changes_updated_at():
    saved = profile_repository.create(_minimal_profile())
    updated = profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Cambiado"))
    assert updated["updated_at"] != saved["updated_at"]


def test_failed_update_does_not_change_updated_at(monkeypatch):
    saved = profile_repository.create(_minimal_profile())
    calls = {"n": 0}

    def _push(path):
        calls["n"] += 1
        return calls["n"] > 1  # B falla, la compensación A confirma

    monkeypatch.setattr(profile_repository.remote_store, "push_path", _push)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileStorageError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Cambiado"))
    current = profile_repository.get(saved["profile_id"])
    assert current["updated_at"] == saved["updated_at"]


def test_update_preserves_profile_id_and_schema_version():
    saved = profile_repository.create(_minimal_profile())
    updated = profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Cambiado"))
    assert updated["profile_id"] == saved["profile_id"]
    assert updated["schema_version"] == saved["schema_version"]


def test_update_on_missing_profile_raises_not_found():
    with pytest.raises(ProfileNotFoundError):
        profile_repository.update("does-not-exist", 1, _minimal_profile())


# ============================================================================
# Phase 3.5 — concurrencia optimista (§20 del ticket)
# ============================================================================

def test_matching_expected_revision_allows_the_write():
    saved = profile_repository.create(_minimal_profile())
    updated = profile_repository.update(saved["profile_id"], saved["revision"], _minimal_profile(first_name="OK"))
    assert updated["first_name"] == "OK"


def test_stale_expected_revision_raises_concurrent_modification():
    saved = profile_repository.create(_minimal_profile())
    profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Primero"))
    with pytest.raises(ProfileConcurrentModificationError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Segundo"))


def test_stale_update_performs_zero_local_write():
    saved = profile_repository.create(_minimal_profile(first_name="Original"))
    profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Primero"))
    path = profile_repository._profile_path(saved["profile_id"])
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ProfileConcurrentModificationError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Segundo"))
    after = path.read_text(encoding="utf-8")
    assert after == before  # ni un byte tocado -- el chequeo corre ANTES de cualquier write


def test_stale_update_performs_zero_push_path_calls(monkeypatch):
    saved = profile_repository.create(_minimal_profile())
    profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Primero"))
    calls: list[Path] = []
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: calls.append(path) or True)
    with pytest.raises(ProfileConcurrentModificationError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Segundo"))
    assert calls == []


def test_two_concurrent_updates_from_the_same_revision_exactly_one_succeeds():
    # §20: "Add a real concurrency test with two threads if practical...
    # Do not rely only on sequential simulation if the lock itself is
    # being justified." -- dos threads REALES arrancan desde la MISMA
    # revision leída de antemano (simulando "user 1 abre el form" / "user
    # 2 abre el form" al mismo tiempo) e intentan actualizar
    # simultáneamente; el lock global de update() debe serializarlos de
    # forma que exactamente uno gane.
    saved = profile_repository.create(_minimal_profile(first_name="Original"))
    starting_revision = saved["revision"]
    results: list[str] = []
    barrier = threading.Barrier(2)

    def _attempt(name: str):
        barrier.wait(timeout=5)  # maximiza la chance de colisión real
        try:
            profile_repository.update(saved["profile_id"], starting_revision, _minimal_profile(first_name=name))
            results.append(f"{name}:success")
        except ProfileConcurrentModificationError:
            results.append(f"{name}:concurrent_modification")

    t1 = threading.Thread(target=_attempt, args=("UsuarioUno",))
    t2 = threading.Thread(target=_attempt, args=("UsuarioDos",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    successes = [r for r in results if r.endswith(":success")]
    conflicts = [r for r in results if r.endswith(":concurrent_modification")]
    assert len(successes) == 1
    assert len(conflicts) == 1
    final = profile_repository.get(saved["profile_id"])
    assert final["revision"] == starting_revision + 1


# ============================================================================
# Phase 3.5 — fallos remotos / compensating overwrite (§21/§22 del ticket)
# ============================================================================

class _FakePushPath:
    """Doble de `remote_store.push_path` con memoria de qué contenido
    'llegó' realmente al remoto en cada llamada (`landed`), INDEPENDIENTE
    de qué booleano se devuelve (`returns`) — necesario para modelar
    exactamente el escenario ambiguo del §22: un PUT que sí aterriza pero
    cuyo ack se pierde (`landed=True, returns=False`), distinto de un PUT
    que genuinamente nunca llega (`landed=False, returns=False`)."""

    def __init__(self):
        self.calls: list[Path] = []
        self.remote_content: dict[str, str] = {}
        self._script: list[tuple[bool, bool]] = []

    def queue(self, *, landed: bool, returns: bool) -> None:
        self._script.append((landed, returns))

    def __call__(self, path) -> bool:
        path = Path(path)
        self.calls.append(path)
        landed, returns = self._script.pop(0) if self._script else (True, True)
        if landed:
            self.remote_content[str(path.resolve())] = path.read_text(encoding="utf-8")
        return returns


def test_update_b_push_success_is_durable(monkeypatch):
    fake = _FakePushPath()
    monkeypatch.setattr(profile_repository.remote_store, "push_path", fake)
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    fake.queue(landed=True, returns=True)

    updated = profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Bruno"))

    assert updated["first_name"] == "Bruno"
    key = str(profile_repository._profile_path(saved["profile_id"]).resolve())
    assert json.loads(fake.remote_content[key])["first_name"] == "Bruno"


def test_update_b_fails_rollback_confirmed_leaves_local_a_and_no_b_visible(monkeypatch):
    fake = _FakePushPath()
    monkeypatch.setattr(profile_repository.remote_store, "push_path", fake)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    fake.queue(landed=False, returns=False)  # B nunca llega
    fake.queue(landed=True, returns=True)  # compensación A SÍ confirma

    with pytest.raises(ProfileStorageError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Bruno"))

    current = profile_repository.get(saved["profile_id"])
    assert current["first_name"] == "Ana"
    assert current["revision"] == 1
    key = str(profile_repository._profile_path(saved["profile_id"]).resolve())
    assert json.loads(fake.remote_content[key])["first_name"] == "Ana"  # nunca "Bruno" visible en el remoto


def test_update_b_fails_rollback_also_fails_raises_remote_outcome_unknown(monkeypatch):
    fake = _FakePushPath()
    monkeypatch.setattr(profile_repository.remote_store, "push_path", fake)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    fake.queue(landed=False, returns=False)  # B falla
    fake.queue(landed=False, returns=False)  # la compensación A TAMBIÉN falla

    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Bruno"))

    # Phase 3.5 §11: el LOCAL igual queda en A -- nunca en B, aunque el
    # remoto haya quedado genuinamente desconocido.
    current = profile_repository.get(saved["profile_id"])
    assert current["first_name"] == "Ana"
    assert current["revision"] == 1


def test_no_delete_path_is_ever_called_during_update_failure(monkeypatch):
    # §4 del ticket: "Absolutely no delete_path() as compensation for
    # update." -- ni en el caso de rollback confirmado ni en el de
    # rollback fallido.
    fake = _FakePushPath()
    monkeypatch.setattr(profile_repository.remote_store, "push_path", fake)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    delete_calls: list = []
    monkeypatch.setattr(profile_repository.remote_store, "delete_path", lambda path: delete_calls.append(path))
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))

    fake.queue(landed=False, returns=False)
    fake.queue(landed=False, returns=False)
    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Bruno"))
    assert delete_calls == []


def test_update_disabled_raises_config_incomplete_and_restores_local_a(monkeypatch):
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)

    with pytest.raises(ProfileConfigIncompleteError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Bruno"))

    current = profile_repository.get(saved["profile_id"])
    assert current["first_name"] == "Ana"  # nunca queda en B sin confirmar
    assert current["revision"] == 1


def test_ambiguous_first_put_then_successful_compensating_overwrite_converges_to_a(monkeypatch):
    # LA PRUEBA CRÍTICA del ticket (§22): el primer push(B) devuelve False
    # pero el contenido "llegó" igual al remoto (ack perdido, no un fallo
    # real) -- la compensación push(A) confirma. El estado remoto FINAL
    # debe ser A, pese a que B haya llegado a existir transitoriamente.
    fake = _FakePushPath()
    monkeypatch.setattr(profile_repository.remote_store, "push_path", fake)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    saved = profile_repository.create(_minimal_profile(first_name="Ana"))

    fake.queue(landed=True, returns=False)  # B SÍ aterriza, pero el ack se pierde
    fake.queue(landed=True, returns=True)  # la compensación A aterriza y confirma

    with pytest.raises(ProfileStorageError):
        profile_repository.update(saved["profile_id"], 1, _minimal_profile(first_name="Bruno"))

    key = str(profile_repository._profile_path(saved["profile_id"]).resolve())
    final_remote = json.loads(fake.remote_content[key])
    assert final_remote["first_name"] == "Ana"  # el remoto CONVERGE a A, no queda en B
    assert profile_repository.get(saved["profile_id"])["first_name"] == "Ana"


# ============================================================================
# Phase 3.8 — create_with_explicit_id(): create-only invariant, idempotencia,
# colisión y concurrencia (§14/§15/§16 del ticket)
# ============================================================================

def _minimal_import_profile(**overrides) -> dict:
    return _minimal_profile(**overrides)


def test_explicit_id_create_succeeds_when_id_does_not_exist():
    profile_id = "11111111-1111-1111-1111-111111111111"
    saved, created = profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile())
    assert created is True
    assert saved["profile_id"] == profile_id
    assert saved["revision"] == 1


def test_explicit_id_create_never_overwrites_an_existing_profile_with_different_data():
    profile_id = "22222222-2222-2222-2222-222222222222"
    profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile(first_name="Original"))
    with pytest.raises(ProfileIdConflictError):
        profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile(first_name="Distinto"))
    # El Profile original queda absolutamente intacto -- zero write.
    current = profile_repository.get(profile_id)
    assert current["first_name"] == "Original"
    assert current["revision"] == 1


def test_explicit_id_create_with_same_payload_is_already_committed_not_a_rewrite():
    profile_id = "33333333-3333-3333-3333-333333333333"
    first, created_first = profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile())
    assert created_first is True
    second, created_second = profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile())
    assert created_second is False  # ALREADY_COMMITTED
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] == first["updated_at"]
    assert second["revision"] == first["revision"] == 1
    assert len(profile_repository.list()) == 1


def test_explicit_id_conflict_raises_before_any_write():
    profile_id = "44444444-4444-4444-4444-444444444444"
    profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile(email="a@x.com"))
    path = profile_repository._profile_path(profile_id)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ProfileIdConflictError):
        profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile(email="b@x.com"))
    after = path.read_text(encoding="utf-8")
    assert after == before  # ni un byte tocado


def test_explicit_id_create_still_uses_the_retry_and_unknown_classification(monkeypatch):
    # Phase 3.6's contract (reintento + REMOTE_OUTCOME_UNKNOWN) se hereda
    # tal cual vía _create_locked -- no una segunda implementación.
    profile_id = "55555555-5555-5555-5555-555555555555"
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: True)
    with pytest.raises(ProfileRemoteOutcomeUnknownError):
        profile_repository.create_with_explicit_id(profile_id, _minimal_import_profile())
    assert profile_repository.get(profile_id) is None  # limpieza -- mismo invariante de Phase 3.1/3.6


def test_two_concurrent_confirmations_of_the_same_explicit_id_exactly_one_created():
    # §16: "Two simultaneous confirmations of the same signed row must
    # not both pass 'not exists' before writing" -- probado con dos
    # threads reales, MISMO profile_id/payload (como si fuera el MISMO
    # token de fila reenviado dos veces en paralelo).
    profile_id = "66666666-6666-6666-6666-666666666666"
    profile = _minimal_import_profile(first_name="Concurrente")
    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _attempt():
        barrier.wait(timeout=5)
        _saved, created = profile_repository.create_with_explicit_id(profile_id, dict(profile))
        results.append(created)

    t1 = threading.Thread(target=_attempt)
    t2 = threading.Thread(target=_attempt)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert sorted(results) == [False, True]  # exactamente uno CREATED, el otro ALREADY_COMMITTED
    all_profiles = [p for p in profile_repository.list() if p["profile_id"] == profile_id]
    assert len(all_profiles) == 1


def test_manual_create_is_unaffected_by_explicit_id_capability():
    # §17: la capability preasignada NUNCA se filtra al alta manual --
    # create() sigue minteando su propio UUID sin importar qué más exista
    # en el módulo.
    saved = profile_repository.create(_minimal_import_profile(first_name="Manual"))
    import uuid as _uuid
    parsed = _uuid.UUID(saved["profile_id"])
    assert str(parsed) == saved["profile_id"]
