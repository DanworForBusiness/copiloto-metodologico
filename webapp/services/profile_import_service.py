"""
profile_import_service.py — Capital Humano Phase 3.8 (Safe Bulk Profile
Import — Preview + Confirm).

UNA SOLA SEMÁNTICA DE CREACIÓN (Phase 3.7 §1/§2, no negociable): este
módulo NUNCA persiste nada directamente — parsea, valida, detecta
duplicados, y arma/verifica el estado de fila firmado. Cada Profile
elegible termina atravesando EXACTAMENTE el mismo camino que el alta
manual: `human_capital_service.validate_and_shape_fields` +
`human_capital_service.find_duplicate` (Phase 3.2/3.5, reusados tal cual,
"no bulk duplicate detection v2") y, en confirm,
`human_capital_service.create_profile_for_import` →
`profile_repository.create_with_explicit_id` (Phase 3.8, la ÚNICA
extensión de superficie que bulk import necesita). Sin bulk repository,
sin persistencia directa de JSON, sin uso directo de `remote_store` acá.

FORMATO: XLSX únicamente (Phase 3.7 §5 — único formato con precedente
real en este Hub, `openpyxl` ya declarado en requirements.txt y usado
tal cual en `staffing_repository.py::get_absences`, MISMO idioma
`load_workbook(io.BytesIO(data), read_only=True, data_only=True)`, en
memoria, nunca persistido a disco). Sin CSV, sin ODS (Phase 3.7 §5/§21).

LÍMITES DE PRIMERA REBANADA (Phase 3.8 §5 — decisión de PRODUCTO/
OPERACIÓN, no un hecho de arquitectura; sin evidencia de un límite Hub-
wide preexistente, ver Phase 3.7 §15/§16 forensic): `MAX_UPLOAD_BYTES`
(2 MiB) y `MAX_IMPORT_ROWS` (500). La lectura del upload está ACOTADA
(`file.read(MAX_UPLOAD_BYTES + 1)` en el router — nunca `file.read()` a
secas, que leería un tamaño arbitrario a memoria antes de poder
rechazarlo) y el conteo de filas se corta apenas se supera el límite
durante la iteración del workbook, nunca después de haber acumulado todo.

PREVIEW — CERO WRITES (Phase 3.7 §7/Phase 3.8 §9): `build_preview()`
nunca llama `profile_repository.create`/`create_with_explicit_id`/
`push_path`, nunca escribe un JSON local. Llama
`human_capital_service.list_profiles()` EXACTAMENTE UNA VEZ por preview
(Phase 3.7 §17/Phase 3.8 §11 — nunca por fila, evitando el defecto N+1
explícitamente señalado) y arma un lookup efímero, LOCAL a esta llamada,
que muere con la request — nunca un `ProfileIndex`/cache persistente.

DUPLICADOS DENTRO DEL ARCHIVO (Phase 3.7 §8/Phase 3.8 §10): MISMO
algoritmo que duplicados contra Profiles existentes
(`human_capital_service.find_duplicate`), aplicado además contra las
filas YA aceptadas de este mismo archivo. La PRIMERA ocurrencia queda
como referencia; toda fila posterior que matchee (contra un Profile
existente O contra una fila anterior del archivo) recibe advisory —
nunca auto-selección/canonicalización, el usuario decide fila por fila.

IDENTIDAD PREASIGNADA + INTEGRIDAD DE ESTADO (Phase 3.8 §12/§13,
CRÍTICO): cada fila elegible recibe un `profile_id` (`uuid4()`) en
preview. Ese id y el payload YA shapeado viajan de vuelta al browser
DENTRO de un token FIRMADO (JWT, `jose.jwt` — el MISMO signer ya usado
para la cookie de sesión en `auth_service.py`, `SESSION_SECRET`/HS256:
"no invented crypto, no new JWT dependency unless already canonical", y
`jose` YA es canónico acá). El browser nunca ve ni puede alterar
`profile_id`/`fields` sin invalidar la firma — confirm verifica la firma
ANTES de leer nada del token, así que el preview state devuelto por el
browser NUNCA se confía a ciegas (condición obligatoria #2 de Phase 3.8).
Un token que no verifica se reporta como fila `INVALID` con un mensaje
que indica volver a la vista previa — nunca una excepción cruda.

SIN import manifest durable (Phase 3.7 §7, decisión ya tomada y
mantenida): el ÚNICO estado de import que sobrevive entre preview y
confirm es el propio token firmado que el browser retiene en el DOM del
resultado de preview — si se pierde (nueva pestaña, sesión reiniciada,
workflow reiniciado desde una carga nueva), el intento de import se
pierde con él y la única opción es un nuevo preview. Esto es una
LIMITACIÓN aceptada de esta primera rebanada, documentada explícitamente
(Phase 3.8 §20), no un bug."""
from __future__ import annotations

import io
import time
import uuid
from dataclasses import dataclass

import openpyxl
from jose import JWTError, jwt

import config
from webapp.services import human_capital_service

# Phase 3.8 §5 — límites de PRIMERA REBANADA, decisión de producto/
# operación (sin evidencia de un límite Hub-wide preexistente que
# heredar, ver forensic Phase 3.7 §15/§16) — deliberadamente modestos,
# nunca presentados como un hecho de arquitectura.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_IMPORT_ROWS = 500

# Contrato de headers (Phase 3.7 §4/§7) — EXACTAMENTE los campos de
# Profile que ya existen, ninguno nuevo. `nombre`/`apellido` son los
# ÚNICOS requeridos (reflejan los únicos campos requeridos de Profile,
# Phase 2 §5/§8). El resto es opcional y puede estar completamente
# ausente del archivo (Phase 3.7 §7: "Preferred: yes -- absent optional
# columns map to null/empty", nunca una plantilla maximal obligatoria).
REQUIRED_HEADERS = ("nombre", "apellido")
OPTIONAL_HEADERS = (
    "email", "telefono", "linkedin", "ubicacion", "perfil_skills",
    "seniority", "fuente", "tags", "notas", "potencial", "marca_potencial",
)
_HEADER_TO_FIELD = {
    "nombre": "first_name", "apellido": "last_name", "email": "email",
    "telefono": "phone", "linkedin": "linkedin_url", "ubicacion": "location",
    "perfil_skills": "profile_skills", "seniority": "seniority", "fuente": "source",
    "tags": "tags", "notas": "notes", "potencial": "potential_rating",
    "marca_potencial": "potential_mark",
}

# Acotado (nunca indefinido) — el tiempo entre "abrí el preview" y
# "confirmé el import" para un usuario real, con margen generoso, MISMO
# criterio que el resto de los timeouts acotados de este módulo (ver
# _RESTORE_WAIT_TIMEOUT_S en profile_repository.py).
_ROW_TOKEN_MAX_AGE_S = 3600  # 1 hora


class ImportFileError(Exception):
    """Fallo de archivo/formato (Phase 3.8 §6) — SIEMPRE un mensaje seguro
    para mostrar al usuario, NUNCA un traceback de openpyxl ni un dump de
    PII. Cubre: archivo faltante/vacío, tamaño excedido, workbook
    ilegible, headers requeridos faltantes, cantidad de filas excedida."""


class RowTokenError(Exception):
    """Token de fila que no verifica (firma inválida/alterada/expirada,
    Phase 3.8 §13/§24) — el caller lo traduce SIEMPRE a un resultado de
    fila `INVALID` con copy que indica volver a la vista previa, nunca
    una excepción cruda hacia el usuario."""


@dataclass
class PreviewRow:
    """Una fila de la vista previa (Phase 3.8 §9) — CERO relación con
    persistencia todavía. `token` es `None` únicamente para filas
    `invalid` (nada que firmar: no hay un `profile_id`/payload válido que
    proteger)."""
    row_number: int
    display_name: str
    fields: dict
    status: str  # "eligible" | "existing_duplicate" | "within_file_duplicate" | "invalid"
    message: str
    profile_id: str | None = None
    token: str | None = None
    duplicate_row_number: int | None = None
    duplicate_display_name: str | None = None


@dataclass
class RowResult:
    """Resultado de UNA fila tras confirm (Phase 3.8 §18/§22). `token` se
    retiene ÚNICAMENTE para `REMOTE_OUTCOME_UNKNOWN` (§19 — permite
    reintentar ESA fila puntual con el MISMO profile_id, nunca minteando
    uno nuevo)."""
    row_number: int
    display_name: str
    status: str
    message: str
    token: str | None = None


def _cell_to_str(value) -> str:
    """Convierte una celda cruda de openpyxl a el string que
    `validate_and_shape_fields` espera — SIN alterar semántica (Phase 3.8
    §8, explícito: nunca convertir un dato inválido en uno válido). Un
    float ENTERO (`1.0`, típico de cómo Excel guarda números aunque el
    usuario haya tipeado "1") se renderiza como `"1"` — pero un float
    genuinamente fraccionario (`1.5`) se renderiza como `"1.5"`, que
    `validate_and_shape_fields`'s `int("1.5")` sigue rechazando tal cual
    lo haría con un `potential_rating` inválido tipeado a mano. NUNCA
    hace `int(1.5)` (eso silenciaría el error, prohibido explícitamente)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_workbook(data: bytes) -> list[dict]:
    """Parsea el XLSX EN MEMORIA (`io.BytesIO`, `read_only=True,
    data_only=True` — MISMO idioma exacto que
    `staffing_repository.py::get_absences`, nunca persistido a disco,
    Phase 3.7 §9/§14). Devuelve una lista de dicts `{header: valor_crudo}`
    por fila de datos (excluye la fila de headers). Filas completamente
    vacías se saltean sin contar para `MAX_IMPORT_ROWS`. Levanta
    `ImportFileError` (mensaje seguro, nunca un traceback de openpyxl)
    para: workbook ilegible, hoja sin fila de headers, headers requeridos
    faltantes, o más de `MAX_IMPORT_ROWS` filas de datos."""
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo de parseo se traduce a un mensaje seguro
        raise ImportFileError("No pudimos leer el archivo. Verificá que sea un XLSX válido.") from exc
    try:
        sheet = workbook.active
        if sheet is None:
            raise ImportFileError("El archivo no tiene ninguna hoja con datos.")
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise ImportFileError("El archivo está vacío.") from None
        headers = [str(h).strip().lower() if h is not None else "" for h in header_row]
        missing = [h for h in REQUIRED_HEADERS if h not in headers]
        if missing:
            raise ImportFileError(f"Faltan columnas requeridas: {', '.join(missing)}.")
        rows: list[dict] = []
        for row_values in rows_iter:
            if row_values is None or all(v is None for v in row_values):
                continue  # fila completamente vacía -- no cuenta para el límite
            if len(rows) >= MAX_IMPORT_ROWS:
                raise ImportFileError(f"El archivo supera el máximo de {MAX_IMPORT_ROWS} filas.")
            rows.append({headers[i]: row_values[i] for i in range(min(len(headers), len(row_values)))})
        return rows
    finally:
        workbook.close()


def _sign_row_token(*, profile_id: str, row_number: int, fields: dict, has_duplicate_advisory: bool) -> str:
    """Firma el estado de UNA fila elegible (Phase 3.8 §12/§13) — MISMO
    signer/algoritmo exacto que `auth_service.create_session`
    (`jose.jwt` + `config.SESSION_SECRET`, HS256). El browser recibe este
    token opaco y lo devuelve tal cual en confirm — cualquier alteración
    de `profile_id`/`fields`/`has_duplicate_advisory` invalida la firma."""
    claims = {
        "profile_id": profile_id,
        "row_number": row_number,
        "fields": fields,
        "has_duplicate_advisory": has_duplicate_advisory,
        "exp": int(time.time()) + _ROW_TOKEN_MAX_AGE_S,
    }
    return jwt.encode(claims, config.SESSION_SECRET, algorithm="HS256")


def verify_row_token(token: str) -> dict:
    """Verifica la firma y devuelve los claims de un token de fila —
    NUNCA confía en un token sin verificar primero (condición obligatoria
    #2 de Phase 3.8: "preview state returned by the browser MUST NOT be
    blindly trusted"). Levanta `RowTokenError` (nunca `JWTError` crudo)
    si la firma no verifica o el token expiró."""
    try:
        return jwt.decode(token, config.SESSION_SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise RowTokenError(
            "El estado de esta fila expiró o no es válido. Volvé a la vista previa e intentá de nuevo."
        ) from exc


def build_preview(rows: list[dict]) -> list[PreviewRow]:
    """Preview de punta a punta (Phase 3.8 §9) — CERO writes: nunca llama
    `profile_repository.create`/`create_with_explicit_id`/`push_path`.

    1. `list_profiles()` EXACTAMENTE UNA VEZ (Phase 3.7 §17/Phase 3.8
       §11) — nunca por fila.
    2. Por fila: mapea headers → campos de Profile, `_cell_to_str` en
       cada celda, `validate_and_shape_fields` (MISMA validación que
       alta/edición manual, Phase 3.8 §8 — sin validador bulk paralelo).
       Fila inválida -> status `invalid`, sin `profile_id`/token (nada
       que persistir, nada que firmar).
    3. Fila válida: `find_duplicate` contra los Profiles YA existentes: si
       matchea, status `existing_duplicate`. Si no, `find_duplicate`
       contra las filas YA aceptadas de este archivo (lookup efímero,
       Phase 3.7 §8/Phase 3.8 §10/§11 — muere con esta llamada): si
       matchea, status `within_file_duplicate`. Si ninguno matchea,
       `eligible`.
    4. Para toda fila válida (eligible o duplicate — nunca invalid):
       `profile_id = uuid4()` preasignado UNA vez, y un token firmado
       (`has_duplicate_advisory=True` para cualquiera de los dos tipos de
       duplicado) para que confirm pueda distinguir sin volver a
       consultar nada."""
    existing_profiles = human_capital_service.list_profiles()
    accepted_in_file: list[tuple[int, str, dict]] = []  # (row_number, display_name, shaped_fields)
    preview_rows: list[PreviewRow] = []

    for row_number, raw_row in enumerate(rows, start=1):
        mapped = {field_name: _cell_to_str(raw_row.get(header)) for header, field_name in _HEADER_TO_FIELD.items()}
        try:
            shaped = human_capital_service.validate_and_shape_fields(mapped)
        except ValueError as exc:
            display_name = f"{mapped.get('first_name', '')} {mapped.get('last_name', '')}".strip() or "(sin nombre)"
            preview_rows.append(PreviewRow(
                row_number=row_number, display_name=display_name, fields=mapped,
                status="invalid", message=str(exc),
            ))
            continue

        display_name = f"{shaped['first_name']} {shaped['last_name']}"
        existing_match = human_capital_service.find_duplicate(shaped, existing_profiles)
        within_file_match: tuple[int, str, dict] | None = None
        if existing_match is None:
            within_file_match = next(
                (entry for entry in accepted_in_file
                 if human_capital_service.find_duplicate(shaped, [entry[2]]) is not None),
                None,
            )

        if existing_match is not None:
            status = "existing_duplicate"
            message = "Ya existe un perfil posiblemente duplicado."
            dup_row_number, dup_display_name = None, f"{existing_match.get('first_name', '')} {existing_match.get('last_name', '')}".strip()
        elif within_file_match is not None:
            status = "within_file_duplicate"
            dup_row_number, dup_display_name, _ = within_file_match
            message = f"Posible duplicado de la fila {dup_row_number} en este mismo archivo."
        else:
            status = "eligible"
            message = "Listo para crear."
            dup_row_number = dup_display_name = None

        profile_id = str(uuid.uuid4())
        token = _sign_row_token(
            profile_id=profile_id, row_number=row_number, fields=shaped,
            has_duplicate_advisory=status != "eligible",
        )
        preview_rows.append(PreviewRow(
            row_number=row_number, display_name=display_name, fields=shaped,
            status=status, message=message, profile_id=profile_id, token=token,
            duplicate_row_number=dup_row_number, duplicate_display_name=dup_display_name,
        ))
        accepted_in_file.append((row_number, display_name, shaped))

    return preview_rows


def confirm_rows(all_tokens: list[str], included_tokens: list[str]) -> list[RowResult]:
    """Confirma las filas cuyo token está en `included_tokens` (Phase 3.8
    §18/§21) — cada una atraviesa `human_capital_service.
    create_profile_for_import`, EXACTAMENTE el mismo camino de
    persistencia que el alta manual salvo la identidad preasignada.
    Filas presentes en `all_tokens` pero AUSENTES de `included_tokens`
    (el usuario las dejó sin marcar — típicamente duplicados que decidió
    no confirmar, Phase 3.7 §8/Phase 3.8 §21: "no auto-upsert") se
    reportan como `DUPLICATE_SKIPPED` sin ningún intento de persistencia.

    No detiene el batch por una fila independiente (Phase 3.7 §9/Phase
    3.8 §18) — cada fila es un Profile independiente."""
    included = set(included_tokens)
    results: list[RowResult] = []
    for token in all_tokens:
        try:
            claims = verify_row_token(token)
        except RowTokenError as exc:
            results.append(RowResult(row_number=-1, display_name="", status="INVALID", message=str(exc)))
            continue
        row_number = claims["row_number"]
        fields = claims["fields"]
        profile_id = claims["profile_id"]
        display_name = f"{fields.get('first_name', '')} {fields.get('last_name', '')}".strip()

        if token not in included:
            results.append(RowResult(
                row_number=row_number, display_name=display_name, status="DUPLICATE_SKIPPED",
                message="No se creó -- no se confirmó esta fila.",
            ))
            continue

        try:
            _profile, created = human_capital_service.create_profile_for_import(profile_id, fields)
        except human_capital_service.ProfileIdConflictError as exc:
            results.append(RowResult(row_number, display_name, "PROFILE_ID_CONFLICT", str(exc)))
        except human_capital_service.ProfileRemoteOutcomeUnknownError as exc:
            # Phase 3.8 §19: retiene el MISMO token para poder reintentar
            # ESTA fila puntual, sin mintear un profile_id nuevo.
            results.append(RowResult(row_number, display_name, "REMOTE_OUTCOME_UNKNOWN", str(exc), token=token))
        except human_capital_service.ProfileStorageError as exc:
            # Incluye ProfileConfigIncompleteError (subclase) -- Phase 3.8
            # §18 no distingue config_incomplete como bucket propio a
            # nivel de fila, se reporta como SOURCE_ERROR (persistencia no
            # confirmada) igual que cualquier otro fallo de push.
            results.append(RowResult(row_number, display_name, "SOURCE_ERROR", str(exc)))
        else:
            status = "CREATED" if created else "ALREADY_COMMITTED"
            message = "Perfil creado." if created else "Ya se había confirmado en un intento anterior de este import."
            results.append(RowResult(row_number, display_name, status, message))
    return results


def summarize(results: list[RowResult], *, total_rows: int, invalid_count: int) -> dict:
    """Resumen agregado (Phase 3.8 §22) — SOLO conteos, nunca PII en el
    agregado (el detalle por fila, con nombre/mensaje, es responsabilidad
    del router/template, nunca de un log)."""
    counts = {status: 0 for status in (
        "CREATED", "ALREADY_COMMITTED", "DUPLICATE_SKIPPED", "SOURCE_ERROR",
        "REMOTE_OUTCOME_UNKNOWN", "PROFILE_ID_CONFLICT", "INVALID",
    )}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    counts["INVALID"] += invalid_count
    return {"total_rows": total_rows, **counts}
