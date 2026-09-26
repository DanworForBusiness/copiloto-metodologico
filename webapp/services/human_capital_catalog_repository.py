"""
human_capital_catalog_repository.py — CCHH Catalog Foundation.

Catálogos GOBERNADOS de Capital Humano (valores de negocio que CCHH mantiene
en un Excel de SharePoint) servidos a las páginas SSR como un READ-MODEL
materializado local + Spaces, SIN token en la lectura.

Arquitectura (aprobada por el owner — "Architecture C"):
  - SoR de negocio = `CCHH HUB/Configuracion/Catalogos_CCHH.xlsx` (lo edita CCHH).
  - Un REFRESH controlado (acción explícita token-carrying, MISMO flujo que
    `/catalog/regenerate`) lo lee vía Graph con el token de LECTURA del Hub
    (`User.Read`/`Sites.Read.All`), valida FAIL-CLOSED, y materializa un
    snapshot normalizado a `human-capital/catalogs/catalogos.json`.
  - Las páginas SSR leen SOLO el snapshot (tokenless). Spaces/local es
    materialización técnica, nunca el SoR.

Identidad canónica del workbook = `drive_id` + `item_id` (el path es solo
discovery/configuración descriptiva).

Durabilidad del refresh (máquina de estados aprobada — reusa el idioma de
`profile_repository`: A en memoria, escritura atómica de B, `push_path`, y
verificación por BYTES EXACTOS vía `remote_store.read_object_bytes` — nunca
ETag/tamaño):

    A = snapshot actual (bytes, en memoria) | None si no había
    escribir B local atómico
    push_path(B):
      True  -> SUCCESS
      False -> remote = read_object_bytes(final):
                 remote == B            -> SUCCESS
                 remote == A o ausente  -> restaurar A local, CONFIRMED_FAILURE
                 remote irresoluble /
                   contenido inesperado -> restaurar A local, push_path(A):
                                             True  -> CONFIRMED_FAILURE (KNOWN_A)
                                             False -> REMOTE_OUTCOME_UNKNOWN

`REMOTE_OUTCOME_UNKNOWN` es un estado terminal REAL: cuando verificación Y
compensación fallan, NO se afirma convergencia garantizada."""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from core import microsoft365, remote_store

_logger = logging.getLogger("danwor.hc_catalog")

# --- Contrato de gobierno ---------------------------------------------------
# Los 5 catálogos gobernados (modelo del audit legacy probado). `marca_potencial`
# NO es catálogo gobernado (enum fijo de producto); `potential_rating` es fijo 1-5.
GOVERNED_CATALOGS: tuple[str, ...] = (
    "seniority",
    "fuente",
    "canal_publicacion",
    "area_consultora",
    "modalidad_contratacion",
)

# --- Contrato del workbook --------------------------------------------------
TABLE_NAME = "tblCatalogosCCHH"
HEADERS: tuple[str, ...] = ("catalogo", "valor", "activo", "orden")
_ACTIVO_TRUE = {"si", "sí", "true", "1", "x", "yes", "verdadero"}
_ACTIVO_FALSE = {"no", "false", "0", "falso", "n"}

# --- Contrato del snapshot --------------------------------------------------
SCHEMA_VERSION = 1
_PERSIST_DIR_NAME = "human-capital"
_CATALOGS_SUBDIR = "catalogs"
_SNAPSHOT_FILENAME = "catalogos.json"

_write_lock = threading.Lock()


class CatalogWorkbookError(Exception):
    """Validación FAIL-CLOSED del workbook falló. Lleva la lista EXACTA de
    errores (fila/columna/motivo). NUNCA se materializa un snapshot parcial:
    ante cualquier error se rechaza el candidato entero y se preserva el
    last-known-good."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "workbook inválido")


class CatalogRemoteOutcomeUnknownError(Exception):
    """Estado terminal: ni la verificación por bytes ni la compensación
    pudieron confirmar el estado durable remoto. No se afirma convergencia."""


class CatalogConfigIncompleteError(Exception):
    """La identidad del workbook (`HC_CATALOG_DRIVE_ID`+`HC_CATALOG_ITEM_ID`)
    no está configurada en este entorno — no se puede hacer el refresh."""


class CatalogFetchError(Exception):
    """El READ de Graph del workbook falló (token/permiso/red/archivo)."""


# ---------------------------------------------------------------------------
# Rutas del read-model
# ---------------------------------------------------------------------------
def _catalogs_dir() -> Path:
    return config.BASE_DIR / _PERSIST_DIR_NAME / _CATALOGS_SUBDIR


def snapshot_path() -> Path:
    return _catalogs_dir() / _SNAPSHOT_FILENAME


# ---------------------------------------------------------------------------
# 1. Contrato del workbook — parse + validación FAIL-CLOSED
# ---------------------------------------------------------------------------
def _norm_valor(raw) -> str:
    return " ".join(str(raw).split()) if raw is not None else ""


def _parse_activo(raw) -> bool | None:
    s = str(raw).strip().lower() if raw is not None else ""
    if s in _ACTIVO_TRUE:
        return True
    if s in _ACTIVO_FALSE:
        return False
    return None


def parse_and_validate_workbook(xlsx_bytes: bytes) -> dict[str, list[dict]]:
    """Parsea `Catalogos_CCHH.xlsx` (Excel Table `tblCatalogosCCHH`) y valida
    FAIL-CLOSED. Devuelve `{catalogo: [{"valor","orden"}, ...]}` SOLO con filas
    activas, ordenadas por `orden`. Ante CUALQUIER problema levanta
    `CatalogWorkbookError` con todos los errores — nunca un resultado parcial.

    Se abre en modo normal (no `read_only`) a propósito: openpyxl solo expone
    `ws.tables` fuera de `read_only`. El workbook de catálogos es chico."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=False, data_only=True)
    except Exception as exc:  # noqa: BLE001 — nunca filtrar traceback de openpyxl
        raise CatalogWorkbookError([f"El archivo no es un .xlsx legible: {type(exc).__name__}."]) from exc

    # Localizar la Excel Table por nombre exacto en cualquier hoja.
    table = None
    table_ws = None
    for ws in wb.worksheets:
        tables = getattr(ws, "tables", {}) or {}
        if TABLE_NAME in tables:
            table = tables[TABLE_NAME]
            table_ws = ws
            break
    if table is None:
        raise CatalogWorkbookError([f"No se encontró la tabla '{TABLE_NAME}' en el workbook."])

    cells = list(table_ws[table.ref])  # filas de celdas dentro del rango de la tabla
    if not cells:
        raise CatalogWorkbookError([f"La tabla '{TABLE_NAME}' está vacía."])

    header_cells = cells[0]
    headers = [(_norm_valor(c.value).lower()) for c in header_cells]
    if headers != list(HEADERS):
        raise CatalogWorkbookError([
            f"Headers incorrectos. Esperado {list(HEADERS)}, encontrado {headers}."
        ])

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    collected: dict[str, list[dict]] = {c: [] for c in GOVERNED_CATALOGS}

    for i, row in enumerate(cells[1:], start=2):  # fila 1 = headers; datos desde la 2 (relativo a la tabla)
        values = [c.value for c in row]
        # Fila completamente vacía → se ignora (no cuenta como dato ni como error).
        if all(v is None or str(v).strip() == "" for v in values):
            continue
        catalogo_raw, valor_raw, activo_raw, orden_raw = (values + [None, None, None, None])[:4]
        catalogo = str(catalogo_raw).strip().lower() if catalogo_raw is not None else ""
        valor = _norm_valor(valor_raw)
        activo = _parse_activo(activo_raw)

        row_errs: list[str] = []
        if catalogo not in GOVERNED_CATALOGS:
            row_errs.append(f"catálogo desconocido '{catalogo_raw}'")
        if not valor:
            row_errs.append("valor vacío")
        if activo is None:
            row_errs.append(f"activo inválido '{activo_raw}'")
        try:
            orden = int(str(orden_raw).strip())
        except (TypeError, ValueError):
            orden = None
            row_errs.append(f"orden inválido '{orden_raw}'")

        if catalogo in GOVERNED_CATALOGS and valor:
            key = (catalogo, valor.lower())
            if key in seen:
                row_errs.append(f"duplicado ({catalogo}, '{valor}')")
            seen.add(key)

        if row_errs:
            errors.append(f"fila {i}: " + ", ".join(row_errs))
            continue
        if activo:
            collected[catalogo].append({"valor": valor, "orden": orden})

    if errors:
        raise CatalogWorkbookError(errors)

    for cat in collected:
        collected[cat].sort(key=lambda r: r["orden"])
    return collected


# ---------------------------------------------------------------------------
# 2. Contrato del snapshot
# ---------------------------------------------------------------------------
def build_snapshot(catalogs: dict[str, list[dict]], *, refreshed_by: str, source: dict) -> dict:
    """Arma el snapshot normalizado durable. Todos los catálogos gobernados
    aparecen SIEMPRE (lista vacía si no hay filas) para un contrato estable."""
    return {
        "schema_version": SCHEMA_VERSION,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "refreshed_by": refreshed_by,
        "source": {
            "drive_id": source.get("drive_id", ""),
            "item_id": source.get("item_id", ""),
            **({"etag": source["etag"]} if source.get("etag") else {}),
        },
        "catalogs": {cat: list(catalogs.get(cat, [])) for cat in GOVERNED_CATALOGS},
    }


def _serialize(snapshot: dict) -> bytes:
    # Determinístico (bytes exactos comparables): mismos bytes que se escriben,
    # suben y se releen para verificar.
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# 4. Lectura del read-model (SSR, tokenless, fail-soft)
# ---------------------------------------------------------------------------
def _load_snapshot() -> dict | None:
    path = snapshot_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 — snapshot ilegible = como si no existiera (fail-soft)
        _logger.warning("hc_catalog.snapshot_unreadable reason=%s", type(exc).__name__)
        return None


def catalog_values(catalogo: str) -> list[str]:
    """Valores activos y ordenados de un catálogo gobernado, para el SSR. Nunca
    levanta hacia la vista: si el snapshot falta o el catálogo no está, devuelve
    `[]` y el consumidor decide el fallback."""
    snap = _load_snapshot()
    if not snap:
        return []
    rows = (snap.get("catalogs") or {}).get(catalogo) or []
    return [r["valor"] for r in rows if isinstance(r, dict) and r.get("valor")]


def snapshot_meta() -> dict | None:
    """Metadata del snapshot (refreshed_at/by/source) para la página de config.
    `None` si todavía no se materializó ninguno."""
    snap = _load_snapshot()
    if not snap:
        return None
    return {
        "schema_version": snap.get("schema_version"),
        "refreshed_at": snap.get("refreshed_at"),
        "refreshed_by": snap.get("refreshed_by"),
        "source": snap.get("source", {}),
    }


# ---------------------------------------------------------------------------
# 5. Refresh flow — máquina de estados de durabilidad
# ---------------------------------------------------------------------------
def _write_atomic_bytes(path: Path, data: bytes) -> None:
    """Escritura CRASH-SAFE de bytes: tempfile en el MISMO dir → flush+fsync →
    `os.replace` (MISMO idioma que `profile_repository._write_atomic`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _restore_previous(path: Path, previous: bytes | None) -> None:
    """Devuelve el read-model local al last-known-good A: reescribe A, o borra el
    archivo si no había A (converge a 'sin snapshot')."""
    if previous is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        _write_atomic_bytes(path, previous)


# Estados terminales del refresh (outcome).
SUCCESS = "success"                       # B durablemente confirmado (o local-only en dev sin Spaces)
CONFIRMED_FAILURE = "confirmed_failure"   # convergido a A (last-known-good), B no tomó efecto
REMOTE_OUTCOME_UNKNOWN = "remote_outcome_unknown"  # terminal: no se pudo confirmar


def _persist_snapshot(snapshot: dict) -> str:
    """Materializa `snapshot` con la máquina de estados de durabilidad aprobada.
    Devuelve uno de SUCCESS / CONFIRMED_FAILURE, o levanta
    `CatalogRemoteOutcomeUnknownError` (estado terminal irresoluble)."""
    final = snapshot_path()
    with _write_lock:
        A = final.read_bytes() if final.exists() else None
        B = _serialize(snapshot)
        _write_atomic_bytes(final, B)  # local = B

        if not remote_store.enabled():
            # Dev / sin Spaces: no hay durabilidad remota que confirmar. El write
            # local es todo lo que existe en este entorno. Prod SIEMPRE tiene Spaces.
            return SUCCESS

        if remote_store.push_path(final):
            return SUCCESS

        # push no confirmó → desambiguar por BYTES EXACTOS.
        try:
            remote = remote_store.read_object_bytes(final)  # bytes | None
        except remote_store.RemoteReadError:
            remote = _UNRESOLVED

        if remote is _UNRESOLVED:
            return self_compensate(final, A)
        if remote == B:
            return SUCCESS
        if remote == A or remote is None:
            _restore_previous(final, A)
            return CONFIRMED_FAILURE
        # Contenido remoto inesperado (ni A ni B): tan irresoluble como un
        # read fallido — no podemos afirmar durabilidad de B ni de A.
        return self_compensate(final, A)


_UNRESOLVED = object()


def self_compensate(final: Path, previous: bytes | None) -> str:
    """Rama irresoluble: restaura A local e intenta re-establecer A en remoto.
    `push(A)` confirmado → CONFIRMED_FAILURE (KNOWN_A). Si no → terminal."""
    _restore_previous(final, previous)
    if previous is None:
        # No había A durable que re-establecer y el estado remoto es irresoluble.
        raise CatalogRemoteOutcomeUnknownError(
            "No pudimos confirmar el estado remoto del catálogo y no hay un "
            "snapshot previo con el cual converger."
        )
    if remote_store.push_path(final):
        return CONFIRMED_FAILURE
    raise CatalogRemoteOutcomeUnknownError(
        "No pudimos confirmar el estado remoto del catálogo ni re-establecer el "
        "snapshot previo. Revisá antes de reintentar."
    )


def workbook_source() -> dict:
    """Identidad canónica del workbook (drive_id+item_id); el path es solo
    diagnóstico. Levanta `CatalogConfigIncompleteError` si falta la identidad."""
    drive_id = (config.HC_CATALOG_DRIVE_ID or "").strip()
    item_id = (config.HC_CATALOG_ITEM_ID or "").strip()
    if not (drive_id and item_id):
        raise CatalogConfigIncompleteError(
            "El catálogo de CCHH no está configurado en este entorno "
            "(faltan HC_CATALOG_DRIVE_ID / HC_CATALOG_ITEM_ID)."
        )
    return {"drive_id": drive_id, "item_id": item_id}


def fetch_workbook_bytes(graph_token: str) -> bytes:
    """READ de Graph del workbook por identidad estable (drive_id+item_id) —
    usa el token de LECTURA del Hub (User.Read/Sites.Read.All), NUNCA el scope
    de escritura documental. Devuelve los bytes crudos del .xlsx."""
    src = workbook_source()
    url = (
        f"https://graph.microsoft.com/v1.0/drives/{src['drive_id']}"
        f"/items/{src['item_id']}/content"
    )
    try:
        return microsoft365.graph_get_bytes(url, graph_token)
    except Exception as exc:  # noqa: BLE001 — normalizar el error de urllib para el caller
        raise CatalogFetchError(f"No pudimos leer el workbook de catálogos: {type(exc).__name__}.") from exc


def refresh_catalogs(graph_token: str, *, refreshed_by: str) -> dict:
    """Flujo de refresh end-to-end (trigger token-carrying): resuelve identidad
    → READ de Graph → parse+validación FAIL-CLOSED → snapshot → persistencia
    durable. Propaga `CatalogConfigIncompleteError`/`CatalogFetchError`/
    `CatalogWorkbookError`/`CatalogRemoteOutcomeUnknownError`."""
    source = workbook_source()
    xlsx = fetch_workbook_bytes(graph_token)
    return refresh_from_workbook(xlsx, refreshed_by=refreshed_by, source=source)


def refresh_from_workbook(xlsx_bytes: bytes, *, refreshed_by: str, source: dict) -> dict:
    """Flujo de refresh completo (sin el fetch Graph, que hace el caller con el
    token de lectura): parse+validación FAIL-CLOSED → snapshot → persistencia
    durable. Devuelve `{"outcome": <estado>, "counts": {catalogo: n}}`.
    Levanta `CatalogWorkbookError` (validación) o
    `CatalogRemoteOutcomeUnknownError` (durabilidad terminal)."""
    catalogs = parse_and_validate_workbook(xlsx_bytes)  # fail-closed
    snapshot = build_snapshot(catalogs, refreshed_by=refreshed_by, source=source)
    outcome = _persist_snapshot(snapshot)
    return {
        "outcome": outcome,
        "counts": {cat: len(catalogs.get(cat, [])) for cat in GOVERNED_CATALOGS},
        "refreshed_at": snapshot["refreshed_at"],
    }
