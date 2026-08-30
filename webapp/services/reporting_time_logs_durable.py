"""
reporting_time_logs_durable.py — DURABLE VERSIONED STATE FOR HISTORICAL TIME
LOGS (Integral Cold Start Fase 1, owner ticket, ver HANDOFF).

Scope, deliberadamente ACOTADO (§1 del ticket): SOLO archivos de Time Logs
cuyo AÑO ya está CERRADO (ver `is_closed_year`) — nunca el archivo actual/
en curso, nunca Facturadas, nunca `DeltaCache` (Proyectos/Cliente/Horas por
proyectos/Client Partner), nunca Staffing. Cada uno de esos queda para una
fase propia si el owner la pide — este módulo es deliberadamente angosto
para poder atribuir causalmente cualquier mejora medida (§14 del ticket
original: "we need causal attribution").

TIME LOGS MUTABILITY CONTRACT (owner clarification, NO negociable) — a
diferencia de Proyectos/Cliente/Horas por proyectos (donde SharePoint es
SIEMPRE la autoridad viva en cada request, sin excepción), Time Logs tiene
una regla de negocio real: un AÑO CALENDARIO CERRADO es inmutable para
horas trabajadas. Esto NO es una ventana de tiempo (TTL) — es un hecho de
negocio (el año ya cerró) que sustituye por completo la necesidad de
validar versión/eTag contra SharePoint para ese año específico:

  - `is_closed_year(year)` — PURO, derivado dinámicamente
    (`int(year) < current_year`, NUNCA una lista harcodeada de años) — el
    mismo criterio sobrevive cualquier rollover de calendario sin cambio
    de código ni reinicio de proceso.
  - Un año CERRADO con entrada durable válida se usa DIRECTO — CERO
    llamada de metadata a SharePoint, CERO descarga de contenido, CERO
    parseo (ver `_download_time_logs_rows`/`_download_one_time_logs_file_cached`,
    reporting_repository.py — el short-circuit ocurre ANTES de siquiera
    listar la carpeta cuando el caller conoce sus años de antemano, o
    antes de descargar un item ya descubierto en el camino de vida
    completa).
  - Un año CERRADO sin entrada durable (primera vez, o entrada corrupta/de
    contrato viejo) se adquiere UNA vez de la forma normal (listar +
    descargar + parsear, sin cambios) y se persiste — nunca rompe la
    request si la persistencia falla (best-effort, ver `save_closed_year_entry`).
  - El AÑO EN CURSO (o cualquier año que `is_closed_year` no clasifique
    como cerrado) NUNCA pasa por este módulo — sigue el camino de siempre,
    validado en vivo contra SharePoint en cada request (ver
    `TimeLogsHistoricalCache`, sin cambios). `_infer_time_logs_csv_year`
    (reporting_repository.py, sin cambios) YA resuelve el archivo "actual"
    (sin sufijo de año) al año calendario de HOY — así que nunca puede
    clasificar como cerrado por construcción, sin necesitar un caso
    especial separado acá.

AUTHORIZATION ISOLATION (ver HANDOFF): la key en disco/objeto remoto NUNCA
contiene el `cache_scope` crudo (email/sub) — se hashea
(`_opaque_scope_key`, SHA-256 truncado, no reversible) antes de tocar
filesystem/S3. Nunca se loguea el scope crudo, el drive_item_id, ni la URL
— sólo labels seguros (stage/reason/año), MISMO criterio de seguridad que
`ReportingGraphError`/`AbsenceGraphError` en todo este Hub. El AÑO en sí
(p. ej. "2024") NO es información sensible — es la clave de negocio misma,
igual que ya viaja sin problema en `reporting.perf cartera.horas_proyectos_filter
hot_years=...`.

REUSO DE INFRAESTRUCTURA (§2 del ticket original): CERO cliente S3 nuevo,
CERO Redis/DB, CERO framework de lifecycle nuevo. El directorio local
(`reporting_cache/time_logs_historical/`) es simplemente OTRO directorio
bajo `config.PERSIST_PATHS` — `core/remote_store.py` ya lo sube/baja de
forma completamente genérica (`mirror_up`/`restore`, sin ningún cambio a
esas funciones más allá del tracking de estado agregado en `restore()`).
Este módulo sólo sabe LEER/ESCRIBIR archivos JSON locales de forma
crash-safe — nunca habla con boto3/S3 directamente.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

import config
from core import remote_store

_logger = logging.getLogger("danwor.reporting.perf")

# Contrato de formato — CUALQUIER cambio de FORMA del archivo durable en sí
# (las keys del dict envolvente) exige subir esto. Un mismatch en la
# lectura es SIEMPRE un miss seguro (ver `load_closed_year_entry`), nunca
# un error de request.
_DURABLE_FORMAT_VERSION = 1
# Representa la FORMA del valor canónico persistido — `list[dict]` de filas
# CSV crudas, MISMAS keys que `csv.DictReader` ya produce hoy (Date/
# Client/Project/Task/Email/Duration Decimal, ver
# `TIME_LOGS_REQUIRED_HEADERS`/`_download_one_time_logs_file` en
# reporting_repository.py). Sube esto SOLO si esa forma cambia — nunca por
# un cambio cosmético del archivo durable envolvente (eso es
# `_DURABLE_FORMAT_VERSION`, arriba).
_PARSER_VERSION = 1

# Relativo a `config.BASE_DIR` — registrado en `config.PERSIST_PATHS`, así
# que `remote_store.restore()`/`mirror_up()` lo tratan exactamente igual
# que `case_library`/`feedback_log`/etc., sin código nuevo en ese módulo
# más allá del tracking de estado ya agregado.
_DURABLE_DIR_NAME = "reporting_cache"
_HISTORICAL_SUBDIR = "time_logs_historical"

# Acotado (nunca indefinido, ver `ensure_restored`) — generoso comparado
# con el costo real que evita (~24-27s de descarga+parseo medidos, ver
# HANDOFF), pero corto comparado con dejar una request colgada si el
# restore de arranque nunca termina por algún motivo real.
_RESTORE_WAIT_TIMEOUT_S = 15.0


def is_closed_year(year: str, *, current_year: int | None = None) -> bool:
    """Un año calendario CERRADO (`int(year) < current_year`) es inmutable
    para horas trabajadas (contrato de negocio del owner, ver docstring
    del módulo) — SIEMPRE derivado dinámicamente, NUNCA una lista
    harcodeada de años (así sobrevive cualquier rollover de calendario sin
    cambio de código). `current_year` inyectable SOLO para tests
    determinísticos (MISMO patrón que `compute_horizon_months`,
    staffing_service.py) — sin él, `datetime.now().year`, la MISMA fuente
    de "hoy" que ya usa `_infer_time_logs_csv_year` (reporting_repository.py)
    para el archivo "actual" — nunca dos relojes distintos en el mismo
    módulo. Un `year` no numérico (dato de origen corrupto) nunca se
    considera cerrado — fail-soft hacia el camino de siempre, en vez de
    arriesgar tratar basura como "cerrado para siempre"."""
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return False
    return year_int < (current_year if current_year is not None else datetime.now().year)


def _durable_dir() -> Path:
    return config.BASE_DIR / _DURABLE_DIR_NAME / _HISTORICAL_SUBDIR


def _opaque_scope_key(cache_scope: str) -> str:
    """Hash NO reversible de `cache_scope` (email/sub) — AUTHORIZATION
    ISOLATION (ver HANDOFF): el valor crudo NUNCA toca filesystem/objeto
    remoto/log. Determinístico: el MISMO scope siempre resuelve al MISMO
    archivo, sin necesitar un índice/registro aparte que también tendría
    que protegerse."""
    return hashlib.sha256(f"time_logs_historical:{cache_scope}".encode("utf-8")).hexdigest()[:32]


def _entry_path(cache_scope: str, year: str) -> Path:
    # `year` es la clave de negocio misma (p. ej. "2024") — segura de usar
    # tal cual en el nombre de archivo (siempre 4 dígitos, ya validado por
    # `is_closed_year`/`_infer_time_logs_csv_year` antes de llegar acá).
    return _durable_dir() / _opaque_scope_key(cache_scope) / f"{year}.json"


def restore_elapsed_ms() -> float | None:
    """`None` hasta que el restore de arranque termina al menos una vez en
    este proceso — passthrough fino de `core.remote_store` (§10 del
    ticket: diagnóstico compacto, nunca decide comportamiento)."""
    return remote_store.restore_elapsed_ms()


def ensure_restored() -> None:
    """Espera (acotado, NUNCA indefinido/polling) a que el restore de
    arranque contra DO Spaces termine, SOLO si genuinamente sigue en
    curso — ver `core.remote_store.await_restore`. Llamado UNA sola vez
    por fetch de Time Logs (ver `_download_time_logs_rows`), ANTES de
    intentar cualquier lectura durable — nunca una espera por archivo."""
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def load_closed_year_entry(cache_scope: str, year: str) -> list[dict] | None:
    """Devuelve el valor canónico durable de un año YA PROBADO cerrado por
    el caller (`is_closed_year`, nunca decidido acá — mantiene la decisión
    de negocio en UN solo lugar) — `None` en cualquier caso que no sea un
    HIT limpio: archivo ausente, corrupto, o contrato de formato/parser
    viejo. A diferencia de una cache versionada por eTag, NO hay
    comparación de versión acá — el contrato de inmutabilidad del año
    cerrado ES la garantía de frescura para este camino (ver docstring del
    módulo); nunca una excepción hacia el caller, este es el camino
    auxiliar, jamás el crítico.

    `remote_store.enabled()` en `False` (sin credenciales Spaces — dev
    local, o esta misma suite de tests) es SIEMPRE un miss inmediato, SIN
    tocar filesystem — §2 del ticket ("keep local development graceful
    when PERSIST_ENABLED is disabled"): sin backing store real, escribir/
    leer archivos locales que nunca sincronizan a ningún lado no acelera
    nada — sólo ensuciaría el working tree/entorno de test con archivos
    bajo `reporting_cache/` que nadie pidió."""
    if not remote_store.enabled():
        return None
    path = _entry_path(cache_scope, year)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        _logger.info("reporting.perf time_logs.closed_year_durable_invalid reason=%s", type(exc).__name__)
        return None
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        _logger.info("reporting.perf time_logs.closed_year_durable_invalid reason=%s", type(exc).__name__)
        return None
    if not isinstance(payload, dict):
        _logger.info("reporting.perf time_logs.closed_year_durable_invalid reason=shape")
        return None
    if payload.get("format_version") != _DURABLE_FORMAT_VERSION or payload.get("parser_version") != _PARSER_VERSION:
        _logger.info("reporting.perf time_logs.closed_year_durable_invalid reason=version_mismatch")
        return None
    value = payload.get("value")
    if not isinstance(value, list):
        _logger.info("reporting.perf time_logs.closed_year_durable_invalid reason=shape")
        return None
    return value


def save_closed_year_entry(cache_scope: str, year: str, drive_item_id: str, version: str, rows: list[dict]) -> None:
    """Escritura CRASH-SAFE: archivo temporal en el MISMO directorio →
    flush + fsync → `os.replace` atómico — nunca deja un archivo a medio
    escribir visible para un lector concurrente ni para el próximo boot.
    `drive_item_id`/`version` viajan en el payload SOLO como diagnóstico/
    auditoría (nunca se re-validan al leer, ver `load_closed_year_entry` —
    el contrato de año cerrado es la garantía de frescura, no el eTag).

    Best-effort a propósito: CUALQUIER falla se loguea compacta y NUNCA
    propaga — esto es persistencia auxiliar, nunca el camino crítico que
    ya produjo `rows` reales para responder la request actual.

    `rows` vacío NUNCA se persiste (ver HANDOFF): un archivo histórico
    real jamás debería estar vacío, así que un resultado vacío acá es
    indistinguible de una falla de transporte/parseo fail-soft
    (`_download_one_time_logs_file`) — persistirlo "envenenaría" el
    estado durable de un año cerrado real HASTA SIEMPRE (a diferencia de
    una cache por eTag, acá no hay ningún evento futuro que lo invalide,
    salvo borrar el archivo a mano). Preferible perder la optimización en
    ese caso (no observado en la práctica) que arriesgar servir un vacío
    falso para siempre.

    `remote_store.enabled()` en `False` es SIEMPRE no-op — MISMO criterio
    que `load_closed_year_entry` (§2 del ticket: sin backing store real,
    nunca ensucia disco con archivos que no persisten a ningún lado)."""
    if not rows or not remote_store.enabled():
        return
    path = _entry_path(cache_scope, year)
    payload = {
        "format_version": _DURABLE_FORMAT_VERSION,
        "parser_version": _PARSER_VERSION,
        "year": year,
        "drive_item_id_hash": hashlib.sha256(drive_item_id.encode("utf-8")).hexdigest()[:32],
        "version": version,
        "value": rows,
    }
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        _logger.info("reporting.perf time_logs.closed_year_durable_save_failed reason=%s", type(exc).__name__)
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
