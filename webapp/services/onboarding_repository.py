"""onboarding_repository.py — persistencia de ONBOARDING (seguimiento de la documentación y trámites de ingreso de cada candidatura contratada).

MISMO patrón de storage que `entrevista_repository`/`busqueda_repository` (un JSON por entidad bajo `human-capital/`, write atómico crash-safe, durabilidad
write-through vía `core.remote_store`, lock global para el read-modify-write) — NO un mecanismo nuevo/paralelo.

SEMÁNTICA DE DOMINIO (verbatim de la referencia app-seleccion; `supabase/schema.sql` tabla `onboarding`, `lib/actions/onboarding.ts`, `lib/onboarding.ts`):
  - UNA fila por candidatura (`candidato_id` UNIQUE en la referencia). En el Hub la identidad de esa candidatura es el `application_id` estable de la
    CandidateApplication (Búsquedas): se REFERENCIA, no se inventa un tercer id; el archivo se llama `<application_id>.json`. `profile_id`/`busqueda_id` se
    guardan para resolver el Profile (maestro de datos personales, leído en vivo) y la Búsqueda.
  - Nace SOLO automáticamente (sin alta manual) cuando la candidatura queda en etapa Finalizado con resultado_final = Ingreso (`crearOnboardingSiNoExiste`).
    Idempotente: ver `create_if_absent`. NUNCA se borra si el resultado cambia después (la referencia no lo borra; tampoco hay delete en su UI).
  - Campos: `fecha_ingreso` (default = hoy al crearse), `oferta_cerrada` (texto libre), `area` (se hereda de la Entrevista de esa candidatura y se
    sincroniza al editarla), el estado del checklist (`{clave: bool}` de los 17 ítems canónicos, ver `onboarding_checklist`) y `completado`.
  - `completado` lo RECALCULA cada escritura del checklist (en la referencia, un trigger de Postgres: todos los ítems tildados). Se persiste (no se deriva
    al leer) para que sea estable frente a cambios futuros de la definición del checklist (en la referencia un onboarding ya completo no vuelve atrás
    por sumar un ítem).
  - Tildar un ítem es una escritura ATÓMICA por ítem, sin concurrencia optimista (como el RPC de la referencia): NO incrementa `revision`, que protege
    únicamente la edición del formulario (fecha/oferta/área).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import config
from core import remote_store
from webapp.services import onboarding_checklist as checklist_def

_logger = logging.getLogger("danwor.human_capital")

_SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_ONBOARDING_SUBDIR = "onboarding"
_RESTORE_WAIT_TIMEOUT_S = 15.0

_write_lock = threading.Lock()

# Campos EDITABLES por formulario. El checklist y `completado` tienen operaciones propias.
EDITABLE_FIELDS = ("fecha_ingreso", "oferta_cerrada", "area")


class OnboardingStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class OnboardingConfigIncompleteError(OnboardingStorageError):
    """`remote_store.enabled() is False` — Spaces sin configurar."""


class OnboardingNotFoundError(Exception):
    """Operación sobre un `application_id` sin Onboarding (→ 404 en el router)."""


class OnboardingConcurrentModificationError(Exception):
    """El Onboarding cambió desde que se leyó la `revision` esperada."""


class OnboardingRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


def _onboarding_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _ONBOARDING_SUBDIR


def _onboarding_path(application_id: str) -> Path:
    return _onboarding_dir() / f"{application_id}.json"


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
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
        raise OnboardingStorageError(f"No se pudo escribir el onboarding en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _cleanup_failed_write(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("human_capital.onboarding_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("human_capital.onboarding_remote_cleanup_failed reason=%s", type(exc).__name__)


def _with_defaults(payload: dict) -> dict:
    payload.setdefault("revision", 1)
    payload.setdefault("oferta_cerrada", None)
    payload.setdefault("area", None)
    payload.setdefault("checklist", {})
    payload.setdefault("completado", False)
    return payload


def get(application_id: str) -> dict | None:
    _ensure_restored()
    path = _onboarding_path(application_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OnboardingStorageError(f"No se pudo leer el onboarding: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return _with_defaults(payload) if isinstance(payload, dict) else None


def list() -> list[dict]:  # noqa: A001 — nombre del contrato, no el builtin
    _ensure_restored()
    directory = _onboarding_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise OnboardingStorageError(f"No se pudo listar los onboardings: {exc}") from exc
    out: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # corrupto/ilegible → se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("application_id"):
            out.append(_with_defaults(payload))
    return out


def _require_writable() -> None:
    """`read-only` no habilita escrituras de negocio: rechazo ANTES de tocar el disco (sin efectos parciales). `local` persiste solo en disco; `remote` conserva su contrato."""
    if remote_store.read_only_mode():
        raise OnboardingConfigIncompleteError("La sección de Onboarding está en modo de solo lectura en este entorno: no se pueden guardar cambios.")


def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    _require_writable()
    _write_atomic(path, payload)
    if remote_store.local_only():
        return payload                                   # `local`: escritura física local atómica; sin sincronización con Spaces
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        if previous is None:
            _cleanup_failed_write(path)
        else:
            _write_atomic(path, previous)
        raise OnboardingConfigIncompleteError("La sección de Onboarding todavía no está configurada para este entorno.")
    if previous is None:
        if remote_store.push_path(path):  # reintento del create
            return payload
        _cleanup_failed_write(path)
        raise OnboardingRemoteOutcomeUnknownError(
            "No pudimos confirmar si este onboarding se guardó. Revisá el listado antes de reintentar.")
    _write_atomic(path, previous)
    if remote_store.push_path(path):
        raise OnboardingStorageError(
            "No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior del onboarding.")
    raise OnboardingRemoteOutcomeUnknownError(
        "No pudimos confirmar el estado remoto de este onboarding tras un fallo de guardado.")


def delete(application_id: str) -> bool:
    """Borrado físico durable (local + remoto) del Onboarding de una postulación. `True` si existía y se borró; `False` si no había Onboarding (idempotente). MISMO contrato que
    `entrevista_repository.delete`: si el borrado remoto no se confirma, ROLLBACK del borrado local (se reescribe el contenido previo) para que local y remoto nunca diverjan."""
    with _write_lock:
        previous = get(application_id)
        if previous is None:
            return False
        _require_writable()
        path = _onboarding_path(application_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise OnboardingStorageError(f"No se pudo eliminar el onboarding: {exc}") from exc
        if remote_store.local_only() or remote_store.delete_path(path):
            return True                                  # `local`: el borrado físico local es el resultado final (sin llamar a Spaces)
        _write_atomic(path, previous)                    # rollback: local vuelve a existir
        if not remote_store.enabled():
            raise OnboardingConfigIncompleteError("La sección de Onboarding todavía no está configurada para este entorno.")
        raise OnboardingRemoteOutcomeUnknownError("No pudimos confirmar si el borrado del onboarding se sincronizó. Revisá el listado antes de reintentar.")


def create_if_absent(*, application_id: str, profile_id: str, busqueda_id: str, area: str | None = None,
                     fecha_ingreso: str | None = None) -> tuple[dict, bool]:
    """Crea el Onboarding de esa candidatura SI NO EXISTE (idempotente; `(doc, creado)`). `fecha_ingreso` default = hoy (MISMO default que la referencia:
    `current_date`). El checklist arranca vacío (nada tildado) y `completado` en False."""
    with _write_lock:
        existing = get(application_id)
        if existing is not None:
            return existing, False
        now = _now_iso()
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "application_id": application_id,
            "profile_id": profile_id,
            "busqueda_id": busqueda_id,
            "revision": 1,
            "fecha_ingreso": fecha_ingreso or date.today().isoformat(),
            "oferta_cerrada": None,
            "area": area,
            "checklist": {},
            "completado": False,
            "created_at": now,
            "updated_at": now,
        }
        return _persist(_onboarding_path(application_id), payload, previous=None), True


def _mutate(application_id: str, mutator, *, expected_revision: int | None = None, bump_revision: bool = True) -> dict:
    with _write_lock:
        current = get(application_id)
        if current is None:
            raise OnboardingNotFoundError(f"El onboarding {application_id} no existe.")
        if expected_revision is not None and current.get("revision", 1) != expected_revision:
            raise OnboardingConcurrentModificationError("Este onboarding cambió desde que abriste el formulario.")
        candidate = json.loads(json.dumps(current))  # copia profunda
        mutator(candidate)
        for immutable in ("application_id", "profile_id", "busqueda_id", "created_at"):
            candidate[immutable] = current.get(immutable)
        candidate["schema_version"] = _SCHEMA_VERSION
        candidate["revision"] = current.get("revision", 1) + (1 if bump_revision else 0)
        candidate["updated_at"] = _now_iso()
        return _persist(_onboarding_path(application_id), candidate, previous=current)


def update(application_id: str, expected_revision: int, fields: dict) -> dict:
    """Edita fecha de ingreso / oferta cerrada / área (formulario). Concurrencia optimista por `revision`."""
    def _apply(doc: dict) -> None:
        for k in EDITABLE_FIELDS:
            if k in fields:
                doc[k] = fields[k]
    return _mutate(application_id, _apply, expected_revision=expected_revision)


def set_item(application_id: str, key: str, checked: bool) -> dict:
    """Tilda/destilda UN ítem canónico (atómico bajo el lock; NO bumpea `revision`) y recalcula `completado` (todos los ítems canónicos tildados)."""
    if not checklist_def.is_item_key(key):
        raise ValueError("Ítem de checklist inexistente.")

    def _apply(doc: dict) -> None:
        state = dict(doc.get("checklist") or {})
        state[key] = bool(checked)
        doc["checklist"] = state
        doc["completado"] = checklist_def.progress(state)["completo"]
    return _mutate(application_id, _apply, bump_revision=False)


def set_area(application_id: str, area: str | None) -> dict | None:
    """Sincronización del área desde la Entrevista de esa candidatura (`sincronizarAreaOnboardingDesdeEntrevista`): si NO hay Onboarding todavía, no hace nada
    (devuelve None). Bumpea `revision` (un formulario abierto debe ver el cambio como concurrente)."""
    try:
        return _mutate(application_id, lambda doc: doc.__setitem__("area", area))
    except OnboardingNotFoundError:
        return None
