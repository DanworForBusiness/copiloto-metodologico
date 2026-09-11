"""commercial_strategy_repository.py — READ + WRITE del documento
"Estrategia Comercial" (Commercial Strategy — real editing pass).

Confirmación de topología (owner): DigitalOcean corre HOY 1 contenedor / 1
proceso uvicorn para esta release — ver docstring de `save()` para el
contrato exacto que esa confirmación habilita. Si el número de instancias
sube, esta clase debe revisarse ANTES de aumentarlo (RLock + revision-check
en memoria de un solo proceso deja de ser suficiente con más de un writer
independiente — ver el storage gate original).

Dos fuentes, un único punto de lectura (`load()`):

  1. SEED committed (`commercial_strategy_data/strategy.yaml`, Phase 1) —
     bootstrap/historia únicamente. Revision 1. Nunca se reescribe en
     runtime, nunca se vuelve a importar del HTML fuente en operación
     normal (eso sigue siendo `scripts/import_commercial_strategy.py`,
     ejecutado a mano, una sola vez).
  2. RUNTIME mutable (`config.COMMERCIAL_STRATEGY_RUNTIME_DIR`, registrada
     en `config.PERSIST_PATHS` — el MISMO mecanismo genérico de
     `core/remote_store.py` que ya usa case_library/feedback_log/etc.) —
     fuente de verdad DESDE el primer `save()` exitoso. Antes de eso, no
     existe — `load()` cae al seed sin escribir nada (el bootstrap del
     archivo runtime es un efecto secundario del PRIMER save exitoso, no
     de leer).

`load()`/`save()` son la ÚNICA frontera que routers/templates deben conocer
— ninguno de los dos sabe la ruta física de ningún archivo.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

import yaml

import config
from core import remote_store

_logger = logging.getLogger("danwor.commercial_strategy")

_SEED_PATH = Path(__file__).parent / "commercial_strategy_data" / "strategy.yaml"
_RUNTIME_PATH = config.COMMERCIAL_STRATEGY_RUNTIME_DIR / "strategy.yaml"

# Startup Restore / Cache Race Gate (ver HANDOFF): `lifespan()` en main.py
# arranca `remote_store.restore()` como background task SIN esperarlo —
# la app queda lista para servir HTTP de inmediato (para que /health no
# falle), mientras el runtime durable sigue bajando de Spaces. Como
# `self._cache` de este módulo se puebla en el PRIMER `load()`/`save()` y
# nunca se vuelve a leer disco después, una request que llega ANTES de que
# el restore termine vería el runtime local todavía ausente, cachearía el
# SEED (revision 1) para el resto de la vida del proceso, e ignoraría para
# siempre el runtime restaurado que aparece segundos después — el MISMO
# problema que ya resolvió `reporting_time_logs_durable.ensure_restored()`
# para Time Logs (ver ese módulo y `core.remote_store.await_restore`).
# Reusa el MISMO primitivo — acotado, nunca polling/sleep arbitrario — en
# vez de inventar una segunda coordinación de restore para esta vertical.
_RESTORE_WAIT_TIMEOUT_S = 15.0


def _ensure_restored() -> None:
    """Espera (acotada) a que el restore de arranque contra DO Spaces
    termine, SOLO si genuinamente sigue en curso — no-op instantáneo si
    Spaces está deshabilitado o el restore ya terminó (`await_restore`
    devuelve de inmediato en ambos casos). Debe llamarse ANTES de
    cualquier lectura de disco que pueble `self._cache` por primera vez."""
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


class CommercialStrategyUnavailable(Exception):
    """El documento no se pudo cargar (archivo ausente o YAML inválido) —
    nunca se fabrica un documento vacío en su lugar."""


class StaleRevisionError(Exception):
    """`save()` con `expected_revision` que ya no coincide con la revision
    actual — otro Guardar ganó la carrera. `current_revision` viaja para que
    el caller arme el 409 sin volver a leer."""

    def __init__(self, current_revision: int):
        super().__init__(f"Revision desactualizada: actual es {current_revision}")
        self.current_revision = current_revision


class DurablePushFailedError(Exception):
    """`save()` escribió localmente pero el push síncrono a Spaces falló —
    el local ya fue revertido a la versión canónica anterior ANTES de que
    esta excepción se levante (ver `save()`). El caller debe reportar error,
    nunca éxito."""


def _read_yaml(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or not data.get("version") or not data.get("revision"):
        raise CommercialStrategyUnavailable(f"Documento sin forma válida en {path}")
    return data


def _dump_yaml(document: dict) -> str:
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100)


class CommercialStrategyRepository:
    """Estado en memoria de UN proceso (ver docstring del módulo — contrato
    de instancia única). `RLock` protege tanto la caché en memoria como el
    archivo runtime en disco contra una carrera entre dos requests
    CONCURRENTES del MISMO proceso — la única concurrencia que existe bajo
    la topología confirmada."""

    def __init__(self, seed_path: Path = _SEED_PATH, runtime_path: Path = _RUNTIME_PATH) -> None:
        self._seed_path = seed_path
        self._runtime_path = runtime_path
        self._lock = RLock()
        self._cache: dict | None = None

    def _read_from_disk(self) -> dict:
        if self._runtime_path.exists():
            try:
                return _read_yaml(self._runtime_path)
            except OSError as exc:
                raise CommercialStrategyUnavailable(f"No se pudo leer {self._runtime_path}: {exc}") from exc
        try:
            return _read_yaml(self._seed_path)
        except OSError as exc:
            raise CommercialStrategyUnavailable(f"No se pudo leer {self._seed_path}: {exc}") from exc

    def load(self) -> dict:
        """Documento canónico completo. Nunca muta el archivo runtime —
        cargar cuando no existe simplemente devuelve el seed (revision 1),
        sin bootstrap de disco (ver docstring del módulo)."""
        with self._lock:
            if self._cache is None:
                _ensure_restored()
                self._cache = self._read_from_disk()
            return copy.deepcopy(self._cache)

    def save(self, updated_fields: dict, *, expected_revision: int, updated_by_id: str, updated_by_label: str) -> dict:
        """Fusiona `updated_fields` (SOLO las claves editables conocidas —
        ver `commercial_strategy_editing.merge_editable_fields`, nunca
        aceptadas acá sin pasar por esa validación primero) sobre el
        documento canónico actual, bajo `expected_revision` optimista.

        Contrato de éxito (owner, storage gate): validar revision → construir
        candidato N+1 → escritura atómica LOCAL → push SÍNCRONO a Spaces →
        SOLO ENTONCES éxito. Si el push falla, el local se revierte a la
        versión canónica anterior ANTES de propagar el error — nunca queda
        una N+1 local sin confirmar en Spaces sirviéndose como si fuera
        canónica (ver `DurablePushFailedError`).

        `remote_store.enabled() == False` (sin credenciales Spaces
        configuradas, MISMO criterio que case_library/feedback_log/etc. en
        TODO este Hub — ver core/remote_store.py docstring: "dev local sin
        cambios") se trata como éxito trivial del push — no hay backend
        durable contra el cual fallar. Con Spaces configurado, un push que
        realmente falla SIEMPRE revierte y nunca reporta éxito."""
        with self._lock:
            if self._cache is None:
                _ensure_restored()
            current = self._cache if self._cache is not None else self._read_from_disk()
            if current.get("revision") != expected_revision:
                raise StaleRevisionError(current.get("revision"))

            candidate = copy.deepcopy(current)
            candidate.update(updated_fields)
            candidate["revision"] = expected_revision + 1
            candidate["updated_at"] = datetime.now(timezone.utc).isoformat()
            candidate["updated_by_id"] = updated_by_id
            candidate["updated_by_label"] = updated_by_label

            previous_bytes = self._runtime_path.read_bytes() if self._runtime_path.exists() else None
            self._runtime_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._runtime_path.with_suffix(".yaml.tmp")
            tmp_path.write_text(_dump_yaml(candidate), encoding="utf-8")
            tmp_path.replace(self._runtime_path)

            pushed = (not remote_store.enabled()) or remote_store.push_path(self._runtime_path)
            if not pushed:
                _logger.error("commercial_strategy.save: push síncrono a Spaces falló — revirtiendo local")
                if previous_bytes is not None:
                    self._runtime_path.write_bytes(previous_bytes)
                else:
                    self._runtime_path.unlink(missing_ok=True)
                raise DurablePushFailedError()

            self._cache = candidate
            return copy.deepcopy(candidate)


_repository = CommercialStrategyRepository()


def _reset_for_tests() -> None:
    """SOLO para tests: descarta la caché en memoria y vuelve a apuntar al
    seed/runtime path por defecto — un test que ensucia `_RUNTIME_PATH` (vía
    monkeypatch de `_repository`) no debe filtrarse a otros tests."""
    global _repository
    _repository = CommercialStrategyRepository()


def load_strategy() -> dict:
    return _repository.load()


def save_strategy(updated_fields: dict, *, expected_revision: int, updated_by_id: str, updated_by_label: str) -> dict:
    return _repository.save(
        updated_fields, expected_revision=expected_revision,
        updated_by_id=updated_by_id, updated_by_label=updated_by_label,
    )
