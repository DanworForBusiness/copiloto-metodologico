"""
reporting_delta_cache.py — DELTA-SYNC CARTERA PROJECT HOURS corrective
(Reporting Cartera/Proyecto Fase 3A, owner ticket, ver HANDOFF).

`DeltaCache` es un primitivo EN PROCESO, thread-safe, single-flight,
acotado por LRU, DISTINTO del par (Fase 2 Time Logs / Fase 3 Facturadas
XLSX, ver reporting_time_logs_cache.py): ese primitivo cachea un valor
INMUTABLE keyed por versión (`eTag`) — un cambio de versión simplemente
recomputa desde cero bajo una key NUEVA. Estado delta es lo opuesto: MUTA
incrementalmente sobre sí mismo (Graph `/items/delta` devuelve solo los
CAMBIOS desde el último checkpoint, nunca la colección completa de nuevo),
así que no hay "versión" que comparar — hay un `@odata.deltaLink` opaco
que se REEMPLAZA en cada sync exitoso.

Por eso `DeltaCache.sync(key, sync_fn)` recibe el ESTADO ACTUAL (o `None`
la primera vez) y `sync_fn` devuelve el ESTADO NUEVO COMPLETO — nunca se
fuerza la abstracción de `TimeLogsHistoricalCache` (ver HANDOFF §7:
"do not force the Time Logs file-version abstraction if it doesn't
naturally fit delta state").

Garantías (MISMA disciplina que Fase 2/3, ver HANDOFF §7):
  - thread-safe;
  - acotado por LRU (`max_entries`);
  - single-flight por key (dos llamadas concurrentes para la MISMA key
    nunca disparan dos syncs — la segunda espera el resultado de la
    primera);
  - CERO estado parcialmente visible: `sync_fn` corre COMPLETO antes de
    que el estado nuevo se adopte — una excepción a mitad de camino deja
    el estado PREVIO (si había) intacto, nunca una mutación a medias;
  - keys DISTINTAS nunca se serializan entre sí (el lock global solo
    protege el diccionario/bookkeeping, `sync_fn` corre FUERA del lock).
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, TypeVar

T = TypeVar("T")

# Fixture real del owner: 1786 filas de "Horas por proyectos". Cada fila es
# un dict Graph chico (`{id, fields: {A_x00f1_o, Mes, HsTrabajadas,
# HsVendidas, HsFacturadas, ProyectoLookupId, EmpresaLookupId, Proyecto}}`)
# — estimado ~500 bytes/fila (overhead de dict + ~8 strings/números
# cortos) ⇒ ~0.9 MB por scope con su snapshot completo cacheado. Con
# `max_entries=40` (MISMO default que Fase 2/3 — ~8-40 usuarios según el
# tamaño real de "Horas por proyectos"), el peor caso teórico ronda
# ~36 MB — un orden de magnitud MENOR que el caso Time Logs (98.5k filas
# históricas), nunca un problema de memoria comparable.
DEFAULT_MAX_ENTRIES = 40


class _Inflight:
    __slots__ = ("event", "ok", "value")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.ok = False
        self.value: object = None


class DeltaCache:
    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES):
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple, object] = OrderedDict()
        self._inflight: dict[tuple, _Inflight] = {}

    def sync(self, key: tuple, sync_fn: Callable[[object | None], T]) -> T:
        """`sync_fn(current_state_or_None) -> new_state`. Corre FUERA del
        lock — keys distintas nunca se bloquean entre sí. El estado nuevo
        SOLO se adopta si `sync_fn` retorna sin excepción; una falla deja
        el estado previo (si existía) exactamente como estaba — nunca una
        mutación parcial visible para el próximo caller."""
        with self._lock:
            current = self._data.get(key)
            inflight = self._inflight.get(key)
            is_leader = inflight is None
            if is_leader:
                inflight = _Inflight()
                self._inflight[key] = inflight

        if not is_leader:
            inflight.event.wait()
            if inflight.ok:
                return inflight.value  # type: ignore[return-value]
            raise inflight.value  # type: ignore[misc]  # misma excepción que vio el leader

        try:
            new_state = sync_fn(current)
        except BaseException as exc:
            with self._lock:
                del self._inflight[key]
            inflight.ok = False
            inflight.value = exc
            inflight.event.set()
            raise

        with self._lock:
            self._data[key] = new_state
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)  # evict LRU
            del self._inflight[key]
        inflight.ok = True
        inflight.value = new_state
        inflight.event.set()
        return new_state

    def clear(self) -> None:
        """SOLO para tests — nunca se llama desde código de producción."""
        with self._lock:
            self._data.clear()
            self._inflight.clear()

    def __len__(self) -> int:
        return len(self._data)
