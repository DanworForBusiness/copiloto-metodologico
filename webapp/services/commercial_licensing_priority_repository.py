"""
commercial_licensing_priority_repository.py — Commercial Licensing, Director
Requests §5-§8: clasificación estratégica de un account_id del Perfil de
licenciamiento (main/"prioritized" vs secondary/"sin prioridad"). Comercial
SHARED state — server-side, sobrevive refresh/relogin, NUNCA
localStorage/sessionStorage/JS-only (owner §7, explícito).

MISMO patrón exacto que `profile_repository.py` (Human Capital): un JSON por
registro, `remote_store.push_path()` write-through inmediato (DATO DE
NEGOCIO real, no cache re-descargable), tempfile+`os.replace` crash-safe.
Simplificado a propósito frente a ese repo: la clasificación es un flag
BINARIO idempotente (deferred / no-deferred), sin `revision`/concurrencia
optimista — no hay "última edición gana" que proteger, "mover a secundario"
o "restaurar" siempre convergen al mismo resultado sin importar el orden.

Diseño minimalista (owner §7 "smallest established server-side mechanism"):
sólo se persiste la EXCEPCIÓN — un archivo por cada account_id que el
Director movió a la tabla secundaria. La ausencia de archivo = está en la
tabla principal (default). Nunca se escribe un registro para cada cuenta
elegible del universo Argentina — sólo para las explícitamente diferidas.

Clave de persistencia: `account_id` CANÓNICO (`res.partner.commercial_
partner_id`/`id` de cuenta, el MISMO id que ya usa el Perfil) — NUNCA el
nombre de cuenta (owner §7, explícito: "Avoid persistence by account
display name")."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import config
from core import remote_store

_logger = logging.getLogger("danwor.commercial")

_PERSIST_DIR_NAME = "commercial-licensing-priority"
_DEFERRED_SUBDIR = "deferred"


class CommercialLicensingPriorityStorageError(Exception):
    """Fallo de persistencia (filesystem local o remote_store/Spaces) al
    mover/restaurar una cuenta — NUNCA silenciado, el caller lo traduce a
    un contrato HTTP explícito (mismo criterio que ProfileStorageError)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_dir() -> Path:
    return Path(config.BASE_DIR) / _PERSIST_DIR_NAME / _DEFERRED_SUBDIR


def _record_path(account_id: int) -> Path:
    return _base_dir() / f"{account_id}.json"


def _write_atomic(path: Path, payload: dict) -> None:
    """MISMO patrón exacto que `profile_repository._write_atomic` —
    tempfile en el MISMO directorio → flush+fsync → `os.replace` atómico."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise CommercialLicensingPriorityStorageError(
            f"No se pudo escribir la clasificación de prioridad en disco: {exc}"
        ) from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _rollback(path: Path, contenido_previo: bytes | None) -> None:
    """Deja el disco local como estaba antes de la operación.

    `contenido_previo is None` = el archivo NO existía, así que revertir es
    borrarlo. Si existía, se repone su contenido EXACTO — una cuenta que ya
    estaba diferida sigue diferida aunque este intento haya fallado.

    Nunca levanta: se invoca mientras ya se está por reportar un error, y
    tapar ese error con otro haría desaparecer la causa real. Si el rollback
    falla, queda logueado y el error original sigue su camino.
    """
    try:
        if contenido_previo is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".rb-", suffix=".json")
            with os.fdopen(fd, "wb") as f:
                f.write(contenido_previo)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
    except OSError:
        _logger.warning(
            "commercial_licensing_priority: rollback local falló para %s", path, exc_info=True)


def _leer_previo(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def list_deferred_account_ids() -> set[int]:
    """Todas las cuentas ACTUALMENTE en la tabla secundaria — lectura
    directa de disco, sin cache propio (el volumen es chico, mismo criterio
    que `profile_repository.list()`). Un archivo corrupto/ilegible se
    salta (logueado), nunca tira abajo el resto del listado."""
    base = _base_dir()
    if not base.is_dir():
        return set()
    result: set[int] = set()
    for entry in base.iterdir():
        if entry.suffix != ".json":
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            result.add(int(data["account_id"]))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            _logger.warning("commercial_licensing_priority: registro ilegible %s", entry, exc_info=True)
            continue
    return result


def defer(account_id: int, *, by: str) -> None:
    """Mueve `account_id` a la tabla secundaria (Director §5/§6:
    "Mover a sin prioridad").

    TRANSACCIONAL DE CARA AL USUARIO: si la durabilidad compartida no se
    confirma, el disco local vuelve a como estaba. Antes no: el archivo local
    se escribía primero y sobrevivía al fallo, así que la acción mostraba un
    error y —como `list_deferred_account_ids()` lee disco local— al refrescar
    la cuenta aparecía diferida igual. Fallaba de cara al usuario y sucedía
    por detrás.

    Idempotente: re-diferir una cuenta ya diferida reescribe el mismo
    registro. Y si ESE intento falla, el registro previo se repone — una
    cuenta ya diferida no se "des-difiere" por un fallo de red.
    """
    path = _record_path(account_id)
    previo = _leer_previo(path)
    payload = {"account_id": account_id, "deferred_at": _now_iso(), "deferred_by": by}
    _write_atomic(path, payload)
    if not remote_store.push_path(path):
        _rollback(path, previo)
        if not remote_store.enabled():
            raise CommercialLicensingPriorityStorageError(
                "La clasificación de prioridad todavía no está configurada para este entorno."
            )
        raise CommercialLicensingPriorityStorageError(
            "No pudimos confirmar el guardado de la clasificación de prioridad. Probá de nuevo."
        )


def restore(account_id: int) -> None:
    """Devuelve `account_id` a la tabla principal (Director §5/§6:
    "Restaurar") — borra el registro de excepción.

    EL ESTADO DURABLE ES AUTORITATIVO. El borrado remoto dejó de ser
    best-effort: si no se confirma, el registro local se REPONE y la cuenta
    sigue diferida para toda la aplicación. Antes el local se borraba igual,
    así que la UI daba la cuenta por restaurada mientras el marcador
    compartido seguía vivo — y un restore de Spaces tras reiniciar el
    contenedor la resucitaba. Para un estado de estrategia COMPARTIDO entre
    usuarios, "se ve restaurada acá pero sigue diferida allá" es peor que un
    error honesto y reintentable.

    Idempotente: restaurar una cuenta que YA está en la principal (sin
    registro) es un no-op exitoso, nunca un error — y nunca toca la red.
    """
    path = _record_path(account_id)
    if not path.exists():
        return
    previo = _leer_previo(path)
    try:
        path.unlink()
    except OSError as exc:
        raise CommercialLicensingPriorityStorageError(
            f"No se pudo borrar la clasificación de prioridad en disco: {exc}"
        ) from exc
    if not remote_store.delete_path(path):
        # La cuenta vuelve a estar diferida: es lo que sigue siendo cierto en
        # el estado compartido.
        _rollback(path, previo)
        if not remote_store.enabled():
            raise CommercialLicensingPriorityStorageError(
                "La clasificación de prioridad todavía no está configurada para este entorno."
            )
        raise CommercialLicensingPriorityStorageError(
            "No pudimos confirmar el cambio de la clasificación de prioridad. Probá de nuevo."
        )