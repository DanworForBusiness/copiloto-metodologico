"""
test_commercial_licensing_priority_repository.py — Commercial Licensing,
Director Requests §5-§8: contrato de
`webapp.services.commercial_licensing_priority_repository`, sin HTTP, sin
Spaces real (todo monkeypatcheado, MISMO patrón que
test_profile_repository.py). Aísla cada test en su propio `tmp_path` (vía
`config.BASE_DIR`)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import config
import pytest

from webapp.services import commercial_licensing_priority_repository as repo


@pytest.fixture(autouse=True)
def _isolated_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _remote_store_noop(monkeypatch):
    # Default determinístico: Spaces deshabilitado, push_path/delete_path
    # devuelven False sin lanzar — los tests que prueban explícitamente el
    # camino de durabilidad remota sobreescriben esto en su propio cuerpo.
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: False)


# ============================================================================
# P/Q/R — mover a secundario
# ============================================================================

def test_p_new_account_starts_absent_ie_in_main_by_default():
    assert repo.list_deferred_account_ids() == set()


def test_q_and_r_defer_removes_from_main_appears_in_secondary(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    assert repo.list_deferred_account_ids() == {42}


def test_defer_is_idempotent_re_deferring_does_not_duplicate(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="a@danwor.com")
    repo.defer(42, by="b@danwor.com")
    assert repo.list_deferred_account_ids() == {42}


def test_defer_without_durable_backend_configured_raises():
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.defer(42, by="director@danwor.com")


def test_b_c_d_defer_fallido_no_deja_residuo_local(monkeypatch):
    """CONTRATO NUEVO (hardening transaccional). Antes el archivo local
    sobrevivía al fallo del push, y como `list_deferred_account_ids()` lee
    disco local, al refrescar la cuenta aparecía diferida pese al error.
    Fallaba de cara al usuario y sucedía por detrás — observado en vivo.

    Ahora: si la durabilidad compartida no se confirma, el disco vuelve a
    como estaba y un refresh sigue mostrando la cuenta activa.
    """
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.defer(42, by="director@danwor.com")
    assert not repo._record_path(42).exists(), "C: no puede quedar marcador local"
    assert repo.list_deferred_account_ids() == set(), "D: un refresh la sigue viendo activa"


def test_e_reintentar_despues_de_un_defer_fallido_funciona(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.defer(42, by="director@danwor.com")
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    assert repo.list_deferred_account_ids() == {42}


def test_un_defer_fallido_no_des_difiere_una_cuenta_QUE_YA_ESTABA_diferida(monkeypatch):
    """El rollback repone el estado PREVIO, no borra a ciegas. Una cuenta ya
    diferida no puede volver a la tabla principal por un fallo de red."""
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    original = repo._record_path(42).read_bytes()

    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: False)
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.defer(42, by="otro@danwor.com")
    assert repo.list_deferred_account_ids() == {42}, "sigue diferida"
    assert repo._record_path(42).read_bytes() == original, "y con su registro intacto"


# ============================================================================
# T/U — restaurar
# ============================================================================

def test_t_and_u_restore_removes_from_secondary_account_returns_to_main(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    assert repo.list_deferred_account_ids() == {42}
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: True)
    repo.restore(42)
    assert repo.list_deferred_account_ids() == set()


def test_restore_is_idempotent_restoring_an_already_main_account_is_a_noop():
    repo.restore(999)  # nunca fue diferida -- no debe tirar
    assert repo.list_deferred_account_ids() == set()


def test_g_h_restore_fallido_deja_la_cuenta_diferida(monkeypatch):
    """CONTRATO NUEVO. El borrado remoto dejó de ser best-effort: para un
    estado de estrategia COMPARTIDO, "se ve restaurada acá pero sigue
    diferida allá" es peor que un error honesto y reintentable.

    Antes, el local se borraba igual y un restore de Spaces tras reiniciar el
    contenedor resucitaba el marcador.
    """
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")

    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: False)
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.restore(42)
    assert repo.list_deferred_account_ids() == {42}, "H: sigue diferida para la aplicación"
    assert repo._record_path(42).exists(), "H: con su marcador repuesto"


def test_i_reintentar_despues_de_un_restore_fallido_funciona(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(42, by="director@danwor.com")
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: False)
    with pytest.raises(repo.CommercialLicensingPriorityStorageError):
        repo.restore(42)
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: True)
    repo.restore(42)
    assert repo.list_deferred_account_ids() == set()


def test_restaurar_una_cuenta_nunca_diferida_no_toca_la_red(monkeypatch):
    """Idempotencia sin efectos: el no-op no puede fallar por storage."""
    llamadas = []
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)
    monkeypatch.setattr(repo.remote_store, "delete_path",
                        lambda path: llamadas.append(path) or False)
    repo.restore(12345)
    assert llamadas == [], "una cuenta sin marcador no dispara borrado remoto"


# ============================================================================
# V — identidad por id CANÓNICO, nunca nombre
# ============================================================================

def test_v_persistence_key_is_account_id_never_a_name(monkeypatch):
    # El schema del registro guardado no tiene ningún campo de "nombre" de
    # cuenta -- sólo `account_id` + metadata de auditoría (quién/cuándo).
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(555, by="director@danwor.com")
    import json
    saved = json.loads(repo._record_path(555).read_text(encoding="utf-8"))
    assert saved["account_id"] == 555
    assert "name" not in saved and "account_name" not in saved


# ============================================================================
# Múltiples cuentas independientes
# ============================================================================

def test_multiple_accounts_deferred_independently(monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(1, by="a@danwor.com")
    repo.defer(2, by="a@danwor.com")
    repo.defer(3, by="a@danwor.com")
    assert repo.list_deferred_account_ids() == {1, 2, 3}
    monkeypatch.setattr(repo.remote_store, "delete_path", lambda path: True)
    repo.restore(2)
    assert repo.list_deferred_account_ids() == {1, 3}


def test_corrupt_record_is_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    repo.defer(1, by="a@danwor.com")
    corrupt_path = repo._base_dir() / "corrupt.json"
    corrupt_path.write_text("not json at all", encoding="utf-8")
    assert repo.list_deferred_account_ids() == {1}

# ============================================================
# M — el estado de prioridad es DATO DE NEGOCIO runtime: vive en Spaces
# (PERSIST_PATHS) y se materializa en disco sólo como staging local.
# No puede entrar a un commit. Se encontró justamente así: un `defer`
# fallido dejó `commercial-licensing-priority/deferred/198.json` en el
# worktree, listo para viajar en el próximo `git add -A`.
# ============================================================

def test_m_el_directorio_runtime_de_prioridad_no_se_puede_stagear():
    """Sigue la MISMA convención que `catalog_local/`, `case_library/` y
    `commercial_strategy_runtime/`: si está en PERSIST_PATHS, está ignorado.
    No alcanza con "hoy no hay archivos" — el directorio aparece en cuanto
    alguien usa la feature."""
    raiz = Path(__file__).resolve().parents[2]
    # Se toma del módulo, no hardcodeado: renombrar el directorio sin tocar
    # el `.gitignore` tiene que romper ACÁ, no en un commit con datos adentro.
    directorio = repo._PERSIST_DIR_NAME

    assert directorio in config.PERSIST_PATHS, (
        "si no se persiste en el store compartido, esto no es dato de negocio"
    )

    git = shutil.which("git")
    if git is None or not (raiz / ".git").exists():
        pytest.skip("sin git disponible para consultar check-ignore")

    # Se consulta por un PATH DE ARCHIVO dentro del directorio: `git
    # check-ignore` sobre el nombre pelado da falsos positivos según haya o
    # no barra final y según el directorio exista.
    r = subprocess.run(
        [git, "check-ignore", "-v", f"{directorio}/deferred/1.json"],
        cwd=str(raiz), capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"`{directorio}/` no está ignorado: un `git add -A` después de usar la "
        f"clasificación de prioridad commitearía estado de negocio de un cliente"
    )
