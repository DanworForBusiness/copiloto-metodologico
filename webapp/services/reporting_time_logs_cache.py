"""
reporting_time_logs_cache.py — CACHE VERSIONED HISTORICAL TIME LOGS
corrective (Reporting Cartera/Proyecto Fase 2, owner ticket, ver HANDOFF).

`TimeLogsHistoricalCache` es un primitivo GENÉRICO (nombre histórico de su
primer uso — Fase 3 lo reusa TAMBIÉN para Facturadas XLSX, ver
`_FACTURADAS_CACHE`/`_facturadas_by_project_cached` en
reporting_repository.py, MISMA clase, instancia separada — nunca
renombrada para minimizar el diff de un cambio puramente cosmético).

Cache EN PROCESO, thread-safe, single-flight, acotada por LRU, de la
representación intermedia CANÓNICA de UNA fuente versionada por driveItem
(`list[dict]` de filas crudas para Time Logs — Date/Client/Project/Task/
Email/Duration Decimal — o `dict[str, float]` ya agregado por proyecto
para Facturadas, ver Fase 3 — CERO fórmula de negocio acá, esto es
puramente un memoize de "ya leí/parseé esta fuente, en esta versión, para
este scope").

Clave: `(scope, drive_item_id, version)`.

  - `scope`: identidad ESTABLE y NO secreta del usuario Hub autenticado —
    el email de la sesión (`auth_service.session_user(request)`, claim
    `sub` del JWT de sesión, ver `webapp/routers/reporting.py`) — NUNCA el
    `graph_token` (nunca se guarda, nunca se loguea, nunca se usa como
    clave). Cachear CROSS-USER sin esto sería servir el archivo de un
    usuario a otro sin haber probado que ambos comparten la MISMA universo
    autorizado de SharePoint — ver AUTHORIZATION ISOLATION en HANDOFF.
    Default a aislamiento total: cada scope tiene su propio namespace de
    entradas dentro de la MISMA cache (nunca se leen entradas de otro
    scope).
  - `drive_item_id`: el `id` de Graph del archivo.
  - `version`: `eTag` del driveItem (preferido — YA viene en la MISMA
    respuesta de listado de carpeta que el código ya hace, sin una segunda
    llamada Graph) o, si faltara, `lastModifiedDateTime:size` (fallback
    conservador — nunca se asume inmutabilidad sin un chequeo de versión).

SOLO archivos HISTÓRICOS (`Time Logs Reports YYYY.csv`, en `Times
previos/`) pasan por acá. El archivo ACTUAL (`Time Logs Reports.csv`,
sin año en el nombre) NUNCA se cachea en esta fase — se descarga siempre
fresco (política conservadora, ver HANDOFF §7: preserva freshness exacta
del año en curso; reconsiderar un TTL corto es una decisión aparte,
posterior a medición real).

Proceso-lifetime, NUNCA persistente (sin Redis/DB/disco) — ver HANDOFF §8:
esta capa no es el primitivo de cache aprobado del repo (no existe uno
todavía), así que no se introduce infraestructura nueva solo para este
corrective. Un restart de proceso simplemente vuelve a frío.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, TypeVar

T = TypeVar("T")

# Cota de memoria (ver HANDOFF §10): el universo histórico real reportado
# por el owner es 5 archivos/proyecto de año (2021..2025). Con
# `max_entries=40` caben ~8 usuarios distintos con su set histórico
# COMPLETO cacheado simultáneamente antes de que el LRU empiece a
# desalojar al menos-usado — deliberadamente conservador (no "todo el
# Hub"), ajustable acá si el smoke de memoria real lo justifica. Estimado
# de footprint: ~98.5k filas históricas totales (113400 - ~13% del archivo
# actual, ver HANDOFF) × ~700 bytes/fila (dict chico de 6 strings cortos)
# ≈ ~69 MB para UN usuario con su historial completo cacheado — con este
# tope, el peor caso teórico (40 archivos simultáneos, mezcla de usuarios)
# se mantiene en un orden de magnitud similar, nunca crecimiento
# desacotado por sesión larga del proceso.
DEFAULT_MAX_ENTRIES = 40


class _Inflight:
    """Un cómputo en curso para UNA key — el objeto en sí (no el dict que
    lo contiene) es lo que un follower referencia, así que remover la key
    del dict de `_inflight` no afecta a followers que ya tomaron su
    referencia antes de esa remoción (ver `get_or_compute`)."""
    __slots__ = ("event", "ok", "value")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.ok = False
        self.value: object = None


class TimeLogsHistoricalCache:
    """Cache LRU con single-flight por key. `get_or_compute(key, compute_fn)`
    devuelve `(value, outcome)` donde `outcome` es uno de:

      - `"hit"`: ya estaba en cache, CERO descarga remota.
      - `"computed"`: esta llamada fue la que efectivamente disparó
        `compute_fn()` (la única descarga remota real para esta key).
      - `"waited"`: otra llamada CONCURRENTE para la MISMA key ya estaba
        computando — esta llamada esperó su resultado (compartido), sin
        disparar una segunda descarga (single-flight, ver HANDOFF §9:
        "prevent duplicate misses where two workers attempt to build the
        same (scope, file, version) simultaneously").

    `compute_fn` corre SIEMPRE fuera del lock global — una key nunca
    bloquea el cómputo de otra key distinta (`HANDOFF §9: "Do NOT serialize
    unrelated files behind one giant global lock"`)."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES):
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple, object] = OrderedDict()
        self._inflight: dict[tuple, _Inflight] = {}

    def get_or_compute(self, key: tuple, compute_fn: Callable[[], T]) -> tuple[T, str]:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key], "hit"  # type: ignore[return-value]
            inflight = self._inflight.get(key)
            is_leader = inflight is None
            if is_leader:
                inflight = _Inflight()
                self._inflight[key] = inflight

        if not is_leader:
            inflight.event.wait()
            if inflight.ok:
                return inflight.value, "waited"  # type: ignore[return-value]
            raise inflight.value  # type: ignore[misc]  # misma excepción que vio el leader

        try:
            value = compute_fn()
        except BaseException as exc:
            with self._lock:
                # Sin entrada válida que dejar — un intento futuro para
                # esta MISMA key debe poder reintentar desde cero, nunca
                # quedar bloqueado por un fallo previo.
                del self._inflight[key]
            inflight.ok = False
            inflight.value = exc
            inflight.event.set()
            raise

        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)  # evict LRU
            del self._inflight[key]
        inflight.ok = True
        inflight.value = value
        inflight.event.set()
        return value, "computed"

    def clear(self) -> None:
        """SOLO para tests — nunca se llama desde código de producción (el
        proceso vive con la cache tibia toda su vida, ver HANDOFF §8)."""
        with self._lock:
            self._data.clear()
            self._inflight.clear()

    def __len__(self) -> int:
        return len(self._data)
