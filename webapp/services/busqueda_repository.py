"""busqueda_repository.py — persistencia de BÚSQUEDAS (procesos de selección).

Una "búsqueda" es lo que la app de referencia (app-seleccion) modela como
`proceso`: un puesto a cubrir con su pipeline de candidatos. Este repositorio
sigue EXACTAMENTE el mismo patrón de almacenamiento que `profile_repository`
(un JSON por entidad bajo `human-capital/`, write atómico crash-safe,
durabilidad write-through vía `core.remote_store`, lock global para el
read-modify-write) — NO un mecanismo de storage nuevo/paralelo.

MODELO (aggregate): cada búsqueda EMBEBE su lista de candidatos
(postulaciones). Un candidato de la búsqueda es la relación explícita
persona↔búsqueda pedida por el owner (CandidateApplication): referencia al
Banco de CVs por `profile_id` (el Banco sigue siendo el maestro de la persona;
acá NUNCA se copian datos personales), + `etapa` del pipeline + `resultado_final`
+ `historial` de transiciones. Una persona puede estar en 0..N búsquedas.

SEMÁNTICA DE DOMINIO (verbatim de la referencia, sin inventar):
  - estado de la búsqueda: 'activo' | 'cerrado'
  - etapas del pipeline (orden): Preselección → Entrevista CCHH → Entrevista
    Líder → Decisión Final → Finalizado
  - resultado_final (SÓLO válido en 'Finalizado'): Ingreso | Rechazado | Desistió
  - modalidad: Híbrido | Remoto | On site (o vacío; los registros históricos se preservan)
  - área de la consultora: OPERACIONES | ADMINISTRACION/CCHH | COMERCIAL (o vacío)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import config
from core import remote_store

_logger = logging.getLogger("danwor.human_capital")

_SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_BUSQUEDAS_SUBDIR = "busquedas"
_RESTORE_WAIT_TIMEOUT_S = 15.0

# Lock global de módulo (MISMO criterio que profile_repository): cubre la
# sección crítica completa de cualquier read-modify-write sobre una búsqueda
# ya conocida (update + mutaciones de pipeline).
_write_lock = threading.Lock()

# ── Constantes de dominio (verbatim de la referencia) ────────────────────────
ESTADOS = ("activo", "cerrado")
ETAPAS = ("Preselección", "Entrevista CCHH", "Entrevista Líder", "Decisión Final", "Finalizado")
ETAPA_INICIAL = "Preselección"
ETAPA_FINAL = "Finalizado"
RESULTADOS_FINALES = ("Ingreso", "Rechazado", "Desistió")
MODALIDADES = ("Híbrido", "Remoto", "On site")

# ── Solicitudes originadas en Operaciones (REG-CH-01) ────────────────────────
# Una solicitud de Operaciones NO es otra entidad: es la MISMA búsqueda con
# tres estructuras aditivas OPCIONALES dentro del documento:
#   - `origen`        -> procedencia (quién/cuándo/desde dónde se creó);
#   - `requerimiento` -> lo que pidió Operaciones (REG-CH-01), tal cual se envió;
#   - `solicitud`     -> flujo de RECEPCIÓN (enviada/observada/en_gestion/
#                        cancelada) + historial de transiciones.
# Son ortogonales a `estado` (activo/cerrado) y al pipeline de candidatos, que
# NO ganan valores nuevos. Los documentos históricos no las tienen y siguen
# funcionando sin migración (se derivan al leer, nunca se escriben como None).
ORIGEN_OPERACIONES = "operaciones"
ORIGEN_CCHH = "cchh"
ORIGENES = (ORIGEN_OPERACIONES, ORIGEN_CCHH)
SOLICITUD_ENVIADA = "enviada"
SOLICITUD_OBSERVADA = "observada"
SOLICITUD_EN_GESTION = "en_gestion"
SOLICITUD_CANCELADA = "cancelada"
SOLICITUD_ESTADOS = (SOLICITUD_ENVIADA, SOLICITUD_OBSERVADA, SOLICITUD_EN_GESTION, SOLICITUD_CANCELADA)
# Matriz de transiciones (única fuente de verdad; el service y los tests la
# consumen). `actor` = TIPO de actor autorizado (la autorización concreta
# vive en los routers futuros); `desde=None` = la solicitud todavía no existe.
SOLICITUD_TRANSICIONES = {
    "enviar":   {"actor": ORIGEN_OPERACIONES, "desde": (None,), "hasta": SOLICITUD_ENVIADA},
    "editar":   {"actor": ORIGEN_OPERACIONES, "desde": (SOLICITUD_ENVIADA,), "hasta": SOLICITUD_ENVIADA},
    "observar": {"actor": ORIGEN_CCHH, "desde": (SOLICITUD_ENVIADA,), "hasta": SOLICITUD_OBSERVADA},
    "reenviar": {"actor": ORIGEN_OPERACIONES, "desde": (SOLICITUD_OBSERVADA,), "hasta": SOLICITUD_ENVIADA},
    "tomar":    {"actor": ORIGEN_CCHH, "desde": (SOLICITUD_ENVIADA,), "hasta": SOLICITUD_EN_GESTION},
    "cancelar": {"actor": ORIGEN_OPERACIONES, "desde": (SOLICITUD_ENVIADA, SOLICITUD_OBSERVADA), "hasta": SOLICITUD_CANCELADA},
}
# Ids de búsqueda que pueden convertirse en nombre de archivo (los históricos
# son uuid4; las solicitudes usan el `submission_id` canónico).
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# `area_departamento` y `area_consultora` NO son enums de este repositorio: son
# catálogos GOBERNADOS (`area` y `area_consultora`, human_capital_catalog_
# repository) — la validación migration-safe vive en el service, MISMO criterio
# que Seniority/Fuente. Por eso acá no hay tuplas de áreas.

# Campos EDITABLES de una búsqueda (los que el form de alta/edición maneja).
# `estado`/`fecha_cierre`/`candidatos` NUNCA se tocan por esta vía — tienen sus
# operaciones propias (close / mutaciones de pipeline).
EDITABLE_FIELDS = (
    "nombre_puesto", "fecha_apertura", "descripcion", "area_departamento",
    "modalidad", "area_consultora", "pedido_formal_recibido", "jd_texto", "palabras_clave",
)


class BusquedaStorageError(Exception):
    """Fallo de PERSISTENCIA (disco o remote_store), nunca de validación."""


class BusquedaConfigIncompleteError(BusquedaStorageError):
    """`remote_store.enabled() is False` — Spaces sin configurar."""


class BusquedaNotFoundError(Exception):
    """Operación sobre una `busqueda_id` inexistente (→ 404 en el router)."""


class BusquedaConcurrentModificationError(Exception):
    """La búsqueda cambió desde que se leyó la `revision` esperada."""


class BusquedaRemoteOutcomeUnknownError(Exception):
    """El estado remoto final no pudo establecerse tras un fallo de push."""


class ApplicationNotFoundError(Exception):
    """Operación sobre una `application_id` que no existe en esa búsqueda."""


class BusquedaInvalidSubmissionIdError(ValueError):
    """`submission_id` ausente o que no es un UUID canónico (nunca se usa como path)."""


class BusquedaSubmissionConflictError(Exception):
    """El `submission_id` ya existe con un contenido (o un dueño) DISTINTO."""


class BusquedaInvalidTransitionError(Exception):
    """Operación no permitida por la matriz de transiciones / guardas de integridad."""


# ── Helpers de storage (MISMO patrón que profile_repository) ─────────────────

def _busquedas_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _BUSQUEDAS_SUBDIR


def _busqueda_path(busqueda_id: str) -> Path:
    if not isinstance(busqueda_id, str) or not _SAFE_ID_RE.match(busqueda_id):
        raise ValueError("Identificador de búsqueda inválido.")  # nunca se arma un path con un id arbitrario
    return _busquedas_dir() / f"{busqueda_id}.json"


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


try:                                                        # fechas de NEGOCIO: America/Argentina/Buenos_Aires (los timestamps técnicos siguen en UTC)
    from zoneinfo import ZoneInfo
    _BUSINESS_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:                                           # noqa: BLE001 — sin base tz (p. ej. Windows sin tzdata): Argentina no tiene horario de verano desde 2009 -> UTC-3 fijo
    _BUSINESS_TZ = timezone(timedelta(hours=-3))


def _business_today() -> str:
    """Fecha de negocio (AAAA-MM-DD) en Buenos Aires: `fecha_apertura` / `fecha_cierre`. Entre 21:00 y 24:00 ART la fecha UTC ya es la del día siguiente."""
    return datetime.now(_BUSINESS_TZ).date().isoformat()


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
        raise BusquedaStorageError(f"No se pudo escribir la búsqueda en disco: {exc}") from exc
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
        _logger.warning("human_capital.busqueda_cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001 — best-effort
        _logger.warning("human_capital.busqueda_remote_cleanup_failed reason=%s", type(exc).__name__)


def _with_revision_default(payload: dict) -> dict:
    payload.setdefault("revision", 1)
    payload.setdefault("candidatos", [])
    return payload


# ── Lecturas ─────────────────────────────────────────────────────────────────

def get(busqueda_id: str) -> dict | None:
    _ensure_restored()
    if not isinstance(busqueda_id, str) or not _SAFE_ID_RE.match(busqueda_id):
        return None  # un id que no puede ser nombre de archivo no existe (→ 404), sin tocar disco
    path = _busqueda_path(busqueda_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo leer la búsqueda: {exc}") from exc
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return _with_revision_default(payload) if isinstance(payload, dict) else None


def list() -> list[dict]:  # noqa: A001 — nombre del contrato, no el builtin
    _ensure_restored()
    directory = _busquedas_dir()
    if not directory.exists():
        return []
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo listar las búsquedas: {exc}") from exc
    out: list[dict] = []
    for entry in entries:
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # corrupto/ilegible → se saltea, no rompe el resto
        if isinstance(payload, dict) and payload.get("busqueda_id"):
            out.append(_with_revision_default(payload))
    return out


# ── Escritura durable (write-through, MISMO contrato que profile_repository) ──

def _persist(path: Path, payload: dict, *, previous: dict | None) -> dict:
    """Escribe `payload` local atómico y confirma durabilidad remota.
    `previous` = el estado anterior a restaurar si la compensación aplica
    (None en un create). Devuelve `payload` en éxito; levanta la excepción de
    clasificación que corresponda si la durabilidad no se confirma."""
    _write_atomic(path, payload)
    if remote_store.push_path(path):
        return payload
    if not remote_store.enabled():
        # Determinístico: nunca "unknown" en este entorno.
        if previous is None:
            _cleanup_failed_write(path)
        else:
            _write_atomic(path, previous)
        raise BusquedaConfigIncompleteError(
            "La sección de Búsquedas todavía no está configurada para este entorno."
        )
    # Spaces habilitado: intento de compensación / limpieza.
    if previous is None:
        if remote_store.push_path(path):  # reintento del create
            return payload
        _cleanup_failed_write(path)
        raise BusquedaRemoteOutcomeUnknownError(
            "No pudimos confirmar si esta búsqueda se guardó. Revisá el listado antes de reintentar."
        )
    _write_atomic(path, previous)  # el local vuelve a A de inmediato
    if remote_store.push_path(path):
        raise BusquedaStorageError(
            "No pudimos confirmar el respaldo del cambio; se mantuvo la versión anterior de la búsqueda."
        )
    raise BusquedaRemoteOutcomeUnknownError(
        "No pudimos confirmar el estado remoto de esta búsqueda tras un fallo de guardado."
    )


def create(fields: dict, *, actor: str | None = None) -> dict:
    """Crea una búsqueda nueva (uuid propio, `estado='activo'`, sin candidatos).
    `fields` ya viene validado/shapeado por el service. La alta directa de CCHH
    también identifica su `origen` (`actor` puede ser None hasta que el router
    lo pase)."""
    _ensure_restored()
    busqueda_id = str(uuid4())
    now = _now_iso()
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "busqueda_id": busqueda_id,
        "revision": 1,
        "estado": "activo",
        "fecha_cierre": None,
        **{k: fields.get(k) for k in EDITABLE_FIELDS},
        "candidatos": [],
        "origen": {"tipo": ORIGEN_CCHH, "creado_por": actor, "creado_en": now},
        "created_at": now,
        "updated_at": now,
    }
    return _persist(_busqueda_path(busqueda_id), payload, previous=None)


def _mutate(busqueda_id: str, mutator, *, expected_revision: int | None = None) -> dict:
    """Read-modify-write durable bajo el lock global. `mutator(doc)` recibe una
    COPIA mutable del documento actual y la modifica in-place (o levanta). Se le
    bumpea `revision`+`updated_at` y se persiste con compensación. Si
    `expected_revision` se pasa, se valida concurrencia optimista ANTES de mutar."""
    with _write_lock:
        current = get(busqueda_id)
        if current is None:
            raise BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
        if expected_revision is not None and current.get("revision", 1) != expected_revision:
            raise BusquedaConcurrentModificationError(
                "Esta búsqueda cambió desde que abriste el formulario."
            )
        candidate = json.loads(json.dumps(current))  # copia profunda
        mutator(candidate)
        candidate["busqueda_id"] = busqueda_id
        candidate["schema_version"] = _SCHEMA_VERSION
        candidate["created_at"] = current.get("created_at")
        candidate["revision"] = current.get("revision", 1) + 1
        candidate["updated_at"] = _now_iso()
        return _persist(_busqueda_path(busqueda_id), candidate, previous=current)


# Campos que NINGUNA edición general de CCHH puede cambiar cuando la búsqueda nació en Operaciones: la fecha de apertura se genera al enviar (fija desde el primer envío) y el
# pedido formal ya fue recibido por definición (REG-CH-01). El servidor los CONSERVA aunque el formulario envíe otro valor (no rompe el form; el cambio simplemente no aplica).
OPERATIONS_LOCKED_FIELDS = ("fecha_apertura", "pedido_formal_recibido")


def is_operations_originated(doc: dict) -> bool:
    return origen_tipo(doc) == ORIGEN_OPERACIONES or solicitud_of(doc) is not None


def update(busqueda_id: str, expected_revision: int, fields: dict) -> dict:
    """Edita los campos de una búsqueda (form de edición). Concurrencia
    optimista por `revision`. NO toca estado/fecha_cierre/candidatos. En una
    búsqueda originada en Operaciones `OPERATIONS_LOCKED_FIELDS` no se modifican."""
    def _apply(doc: dict) -> None:
        _require_managed(doc, "editar")
        locked = is_operations_originated(doc)
        for k in EDITABLE_FIELDS:
            if k in fields and not (locked and k in OPERATIONS_LOCKED_FIELDS):
                doc[k] = fields[k]
    return _mutate(busqueda_id, _apply, expected_revision=expected_revision)


# ── Mutaciones del pipeline (candidatos embebidos) ──────────────────────────

def _find_app(doc: dict, application_id: str) -> dict:
    for app in doc.get("candidatos", []):
        if app.get("application_id") == application_id:
            return app
    raise ApplicationNotFoundError(f"La postulación {application_id} no existe en esta búsqueda.")


def add_candidate(busqueda_id: str, profile_id: str) -> dict:
    """Asocia un Profile del Banco a la búsqueda (nueva postulación en
    Preselección, con su primera fila de historial). Idempotente-ish: si el
    profile ya tiene una postulación ACTIVA (no finalizada) en ESTA búsqueda,
    no la duplica."""
    def _apply(doc: dict) -> None:
        _require_managed(doc, "agregar candidatos a")
        for app in doc.get("candidatos", []):
            if app.get("profile_id") == profile_id and app.get("etapa") != ETAPA_FINAL:
                return  # ya está en la búsqueda, no duplicar
        now = _now_iso()
        doc.setdefault("candidatos", []).append({
            "application_id": str(uuid4()),
            "profile_id": profile_id,
            "etapa": ETAPA_INICIAL,
            "resultado_final": None,
            "fecha_cambio_etapa": now,
            "historial": [{"etapa": ETAPA_INICIAL, "fecha": now}],
            "created_at": now,
        })
    return _mutate(busqueda_id, _apply)


def move_stage(busqueda_id: str, application_id: str, etapa: str, resultado_final: str | None) -> dict:
    """Mueve una postulación a `etapa` (registrando la transición en historial).
    `resultado_final` se fuerza a None salvo que etapa == Finalizado."""
    def _apply(doc: dict) -> None:
        _require_managed(doc, "mover candidatos de")
        app = _find_app(doc, application_id)
        now = _now_iso()
        app["etapa"] = etapa
        app["resultado_final"] = resultado_final if etapa == ETAPA_FINAL else None
        app["fecha_cambio_etapa"] = now
        app.setdefault("historial", []).append({"etapa": etapa, "fecha": now})
    return _mutate(busqueda_id, _apply)


def remove_candidate(busqueda_id: str, application_id: str) -> dict:
    """Quita una postulación de la búsqueda (el Profile del Banco no se toca)."""
    def _apply(doc: dict) -> None:
        _require_managed(doc, "quitar candidatos de")
        before = doc.get("candidatos", [])
        after = [a for a in before if a.get("application_id") != application_id]
        if len(after) == len(before):
            raise ApplicationNotFoundError(f"La postulación {application_id} no existe en esta búsqueda.")
        doc["candidatos"] = after
    return _mutate(busqueda_id, _apply)


def delete(busqueda_id: str) -> None:
    """Hard delete -- MISMO patrón/contrato de durabilidad exacto que
    `entrevista_repository.delete` (sin precedente en la referencia: `procesos`
    no tiene una acción de borrado ahí, esto es una capacidad propia del Hub,
    owner request explícito). El service (`human_capital_service.delete_busqueda`)
    es quien decide SI se puede borrar (sólo "placeholder": sin postulaciones y
    sin Entrevistas vinculadas) -- este repositorio, como el resto, NUNCA valida
    negocio, sólo persiste. Si el borrado remoto no se confirma, ROLLBACK del
    borrado local (se reescribe el contenido previo) para que local/remoto
    nunca diverjan."""
    previous = get(busqueda_id)
    if previous is None:
        raise BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
    if isinstance(previous.get("solicitud"), dict):
        # Una solicitud de Operaciones NO se borra físicamente: se cancela (queda trazable).
        raise BusquedaInvalidTransitionError(
            "Una solicitud originada en Operaciones no se puede eliminar; se cancela y queda en el historial."
        )
    path = _busqueda_path(busqueda_id)
    try:
        path.unlink()
    except FileNotFoundError:
        raise BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.") from None
    except OSError as exc:
        raise BusquedaStorageError(f"No se pudo eliminar la búsqueda: {exc}") from exc
    if remote_store.delete_path(path):
        return
    if not remote_store.enabled():
        raise BusquedaConfigIncompleteError(
            "La sección de Búsquedas todavía no está configurada para este entorno."
        )
    _write_atomic(path, previous)  # rollback: local vuelve a existir
    raise BusquedaRemoteOutcomeUnknownError(
        "No pudimos confirmar si el borrado se sincronizó. Revisá el listado antes de reintentar."
    )


def close(busqueda_id: str, finalize_application_ids: list[str] | None = None) -> dict:
    """Cierra la búsqueda: las postulaciones pendientes indicadas pasan a
    Finalizado · Rechazado, y la búsqueda queda `estado='cerrado'` con
    `fecha_cierre`=hoy."""
    finalize = set(finalize_application_ids or [])
    def _apply(doc: dict) -> None:
        _require_managed(doc, "cerrar")
        now = _now_iso()
        for app in doc.get("candidatos", []):
            if app.get("application_id") in finalize and app.get("etapa") != ETAPA_FINAL:
                app["etapa"] = ETAPA_FINAL
                app["resultado_final"] = "Rechazado"
                app["fecha_cambio_etapa"] = now
                app.setdefault("historial", []).append({"etapa": ETAPA_FINAL, "fecha": now})
        doc["estado"] = "cerrado"
        doc["fecha_cierre"] = _business_today()
    return _mutate(busqueda_id, _apply)


# ══════════════════════════════════════════════════════════════════════════════
# SOLICITUDES DE OPERACIONES (REG-CH-01) — mismo agregado, mismo storage.
# El service es DUEÑO de la validación/normalización del contenido; acá sólo se
# persiste con las MISMAS garantías (lock, revision, write-through, compensación)
# más la máquina de estados de recepción y la idempotencia por `submission_id`.
# ══════════════════════════════════════════════════════════════════════════════

# ── Lecturas derivadas (históricos sin estructuras nuevas, sin migración) ────

def solicitud_of(doc: dict) -> dict | None:
    """El bloque `solicitud` o None (búsqueda histórica / alta directa de CCHH)."""
    sol = doc.get("solicitud")
    return sol if isinstance(sol, dict) else None


def solicitud_estado(doc: dict) -> str | None:
    sol = solicitud_of(doc)
    return sol.get("estado") if sol else None


def origen_tipo(doc: dict) -> str | None:
    """`operaciones` | `cchh` | None (histórica, sin `origen`)."""
    origen = doc.get("origen")
    return origen.get("tipo") if isinstance(origen, dict) else None


SOLICITUD_CATEGORIAS = {
    SOLICITUD_ENVIADA: "pendiente", SOLICITUD_OBSERVADA: "observada",
    SOLICITUD_EN_GESTION: "gestionada", SOLICITUD_CANCELADA: "cancelada",
}


def solicitud_categoria(doc: dict) -> str | None:
    """Categoría para listados/métricas: `pendiente` (enviada, a la espera de CCHH) | `observada` | `gestionada` (tomada por CCHH) | `cancelada`. None si NO es una solicitud
    (histórica o alta directa de CCHH). Es una lectura derivada: no se persiste."""
    return SOLICITUD_CATEGORIAS.get(solicitud_estado(doc))


def es_gestionada(doc: dict) -> bool:
    """True para lo que CCHH efectivamente gestiona: búsquedas sin solicitud (históricas / alta directa) y solicitudes ya tomadas en gestión. False para pendientes, observadas y canceladas."""
    return solicitud_estado(doc) in (None, SOLICITUD_EN_GESTION)


def solicitud_reenvios(doc: dict) -> int:
    sol = solicitud_of(doc)
    return sum(1 for h in ((sol or {}).get("historial") or []) if h.get("transicion") == "reenviar")


def solicitud_reenviada(doc: dict) -> bool:
    """Indicador DERIVADO (no es un estado): la solicitud está `enviada` de nuevo tras haber sido observada y corregida por Operaciones."""
    return solicitud_estado(doc) == SOLICITUD_ENVIADA and solicitud_reenvios(doc) > 0


def find_submission(submission_id: str) -> dict | None:
    """La solicitud ya aceptada con ese `submission_id` (local o, si hay remoto, reconciliada con el bucket) o None. Sirve para decidir la idempotencia ANTES de validar contra catálogos
    que pueden haber cambiado. Remoto indeterminable -> `BusquedaRemoteOutcomeUnknownError` (nunca se asume que no existe)."""
    _ensure_restored()
    busqueda_id = normalize_submission_id(submission_id)
    path = _busqueda_path(busqueda_id)
    with _write_lock:
        existing = get(busqueda_id)
        if existing is None:
            existing = _read_remote_if_any(path, busqueda_id)
        return existing


def normalize_submission_id(value) -> str:
    """UUID canónico (minúsculas, con guiones) o `BusquedaInvalidSubmissionIdError`.
    Es la ÚNICA puerta por la que un id externo puede terminar en un path."""
    if not isinstance(value, str):
        raise BusquedaInvalidSubmissionIdError("El identificador de envío (submission_id) es obligatorio.")
    raw = value.strip()
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        raise BusquedaInvalidSubmissionIdError("El identificador de envío (submission_id) no es un UUID válido.") from None
    canonical = str(parsed)
    if canonical != raw.lower() or parsed.int == 0 or parsed.variant != uuid.RFC_4122:
        raise BusquedaInvalidSubmissionIdError("El identificador de envío (submission_id) no es un UUID válido.")
    return canonical


def content_fingerprint(requerimiento: dict, actor: str) -> str:
    """sha256 del contenido canónico ENVIADO (requerimiento ya normalizado +
    solicitante). Excluye todo lo generado por el servidor (fechas, revisión)."""
    canonical = json.dumps({"requerimiento": requerimiento, "solicitante": actor},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Guardas de integridad ────────────────────────────────────────────────────

def _require_managed(doc: dict, accion: str) -> None:
    """Las operaciones propias de CCHH (editar/cerrar/agregar candidatos) sólo
    aplican a búsquedas SIN solicitud o con la solicitud YA tomada en gestión."""
    estado = solicitud_estado(doc)
    if estado is None or estado == SOLICITUD_EN_GESTION:
        return
    if estado == SOLICITUD_CANCELADA:
        raise BusquedaInvalidTransitionError(f"No se puede {accion} una solicitud cancelada.")
    raise BusquedaInvalidTransitionError(
        f"No se puede {accion} la búsqueda mientras la solicitud está '{estado}': primero tiene que tomarla en gestión CCHH."
    )


def _require_transition(doc: dict, nombre: str) -> dict:
    """Valida `nombre` contra la matriz y devuelve el bloque `solicitud`."""
    sol = solicitud_of(doc)
    if sol is None:
        raise BusquedaInvalidTransitionError("Esta búsqueda no es una solicitud originada en Operaciones.")
    estado = sol.get("estado")
    if estado not in SOLICITUD_TRANSICIONES[nombre]["desde"]:
        if estado == SOLICITUD_CANCELADA:
            detalle = "La solicitud está cancelada."
        elif estado == SOLICITUD_EN_GESTION:
            detalle = "La solicitud ya está en gestión en Capital Humano."
        else:
            detalle = f"La solicitud está '{estado}'."
        raise BusquedaInvalidTransitionError(f"No se puede '{nombre}': {detalle}")
    return sol


def _check_revision(expected_revision) -> int:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("Falta la revisión de la solicitud (control de concurrencia).")
    return expected_revision


def _check_actor(actor) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Falta identificar quién realiza la operación.")
    return actor.strip().lower()


def _record(sol: dict, nombre: str, desde: str | None, actor: str, now: str, motivo: str | None = None) -> None:
    spec = SOLICITUD_TRANSICIONES[nombre]
    entry = {"transicion": nombre, "desde": desde, "hasta": spec["hasta"],
             "actor": actor, "actor_tipo": spec["actor"], "fecha": now}
    if motivo:
        entry["motivo"] = motivo
    sol["estado"] = spec["hasta"]
    sol.setdefault("historial", []).append(entry)


def _mirror_requerimiento(doc: dict, requerimiento: dict) -> None:
    """El requerimiento enviado es el registro de lo pedido; los campos
    equivalentes de la búsqueda (los que CCHH ya ve/lista) se espejan."""
    doc["requerimiento"] = requerimiento
    doc["nombre_puesto"] = requerimiento.get("nombre_puesto")
    doc["area_consultora"] = requerimiento.get("area_consultora")
    doc["modalidad"] = requerimiento.get("modalidad")


# ── Alta idempotente + reconciliación ────────────────────────────────────────

def _read_remote_if_any(path: Path, busqueda_id: str) -> dict | None:
    """Antes de crear, desambigua un intento previo de resultado incierto:
    ¿ese objeto ya quedó en el bucket? Sólo si hay persistencia remota.
    Ausente confirmado → None. No determinable → `BusquedaRemoteOutcomeUnknownError`
    (NUNCA se crea a ciegas: podría pisar un envío que sí se guardó)."""
    if not remote_store.enabled():
        return None
    try:
        raw = remote_store.read_object_bytes(path)
    except remote_store.RemoteReadError:
        raise BusquedaRemoteOutcomeUnknownError(
            "No pudimos verificar si esta solicitud ya había sido guardada. Reintentá en unos minutos."
        ) from None
    if raw is None:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        doc = None
    if not isinstance(doc, dict) or doc.get("busqueda_id") != busqueda_id:
        raise BusquedaRemoteOutcomeUnknownError(
            "El registro remoto de esta solicitud no se pudo interpretar; no se sobrescribió nada."
        )
    _write_atomic(path, doc)  # re-hidrata la copia local con el estado remoto real (no inventa nada)
    return _with_revision_default(doc)


def create_solicitud(submission_id: str, requerimiento: dict, actor: str) -> dict:
    """Crea (o reconoce) la solicitud de Operaciones identificada por `submission_id`.
    `busqueda_id == submission_id`: la identidad ES la clave de idempotencia.
      - no existe  → se crea (`solicitud.estado='enviada'`, `fecha_apertura`=hoy UTC);
      - existe con el MISMO contenido original → reenvío idempotente: devuelve la
        búsqueda tal como está hoy (no duplica, no pisa nada);
      - existe con contenido/dueño DISTINTO (incluida una búsqueda que no es
        solicitud) → `BusquedaSubmissionConflictError`.
    `requerimiento` ya viene validado/normalizado por el service."""
    _ensure_restored()
    busqueda_id = normalize_submission_id(submission_id)
    actor = _check_actor(actor)
    fingerprint = content_fingerprint(requerimiento, actor)
    path = _busqueda_path(busqueda_id)
    with _write_lock:
        existing = get(busqueda_id)
        if existing is None:
            existing = _read_remote_if_any(path, busqueda_id)
        if existing is not None:
            sol = solicitud_of(existing)
            if sol is not None and sol.get("submission_fingerprint") == fingerprint:
                return existing
            raise BusquedaSubmissionConflictError(
                "Ese identificador de envío ya se usó con un contenido distinto."
            )
        now = _now_iso()
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "busqueda_id": busqueda_id,
            "revision": 1,
            "estado": "activo",
            "fecha_cierre": None,
            **{k: None for k in EDITABLE_FIELDS},
            "fecha_apertura": _business_today(),
            "pedido_formal_recibido": True,
            "candidatos": [],
            "origen": {"tipo": ORIGEN_OPERACIONES, "creado_por": actor, "creado_en": now},
            "solicitud": {
                "submission_id": busqueda_id,
                "submission_fingerprint": fingerprint,
                "estado": SOLICITUD_ENVIADA,
                "solicitante": actor,
                "enviada_en": now,
                "ultimo_envio_en": now,
                "observacion": None,
                "historial": [],
            },
            "created_at": now,
            "updated_at": now,
        }
        _mirror_requerimiento(payload, requerimiento)
        _record(payload["solicitud"], "enviar", None, actor, now)
        return _persist(path, payload, previous=None)


# ── Transiciones (todas con control de revisión, bajo el lock y con compensación) ──

def _transition(busqueda_id: str, expected_revision, nombre: str, actor: str, apply, motivo: str | None = None) -> dict:
    expected_revision = _check_revision(expected_revision)
    actor = _check_actor(actor)

    def _apply(doc: dict) -> None:
        sol = _require_transition(doc, nombre)
        desde = sol.get("estado")
        now = _now_iso()
        apply(doc, sol, now)
        _record(sol, nombre, desde, actor, now, motivo)
    return _mutate(busqueda_id, _apply, expected_revision=expected_revision)


def edit_solicitud(busqueda_id: str, expected_revision: int, requerimiento: dict, actor: str) -> dict:
    """Operaciones corrige una solicitud todavía `enviada` (no tomada)."""
    return _transition(busqueda_id, expected_revision, "editar", actor,
                       lambda doc, sol, now: _mirror_requerimiento(doc, requerimiento))


def resubmit_solicitud(busqueda_id: str, expected_revision: int, requerimiento: dict, actor: str) -> dict:
    """Operaciones corrige una solicitud `observada` y la vuelve a enviar."""
    def _apply(doc: dict, sol: dict, now: str) -> None:
        _mirror_requerimiento(doc, requerimiento)
        sol["ultimo_envio_en"] = now
        sol["observacion"] = None  # queda en el historial; deja de estar vigente
    return _transition(busqueda_id, expected_revision, "reenviar", actor, _apply)


def observe_solicitud(busqueda_id: str, expected_revision: int, motivo: str, actor: str) -> dict:
    """CCHH observa una solicitud `enviada` (motivo obligatorio, validado por el service)."""
    actor_n = _check_actor(actor)

    def _apply(doc: dict, sol: dict, now: str) -> None:
        sol["observacion"] = {"motivo": motivo, "por": actor_n, "en": now}
    return _transition(busqueda_id, expected_revision, "observar", actor, _apply, motivo=motivo)


def take_solicitud(busqueda_id: str, expected_revision: int, actor: str) -> dict:
    """CCHH toma en gestión una solicitud `enviada` (desde acá es sólo lectura para Operaciones)."""
    actor_n = _check_actor(actor)

    def _apply(doc: dict, sol: dict, now: str) -> None:
        sol["tomada_por"] = actor_n
        sol["tomada_en"] = now
    return _transition(busqueda_id, expected_revision, "tomar", actor, _apply)


def cancel_solicitud(busqueda_id: str, expected_revision: int, motivo: str | None, actor: str) -> dict:
    """Operaciones cancela (antes de que CCHH la tome). La búsqueda queda
    `estado='cerrado'` (coherencia con el cierre) y la solicitud `cancelada`."""
    actor_n = _check_actor(actor)

    def _apply(doc: dict, sol: dict, now: str) -> None:
        sol["cancelada_por"] = actor_n
        sol["cancelada_en"] = now
        sol["motivo_cancelacion"] = motivo
        doc["estado"] = "cerrado"
        doc["fecha_cierre"] = _business_today()
    return _transition(busqueda_id, expected_revision, "cancelar", actor, _apply, motivo=motivo)
