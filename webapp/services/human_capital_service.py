"""
human_capital_service.py — Capital Humano Phase 3 (First Profile Slice) +
Phase 3.2 (Profile Search, Filters & Duplicate Advisory) + Phase 3.5
(Safe Profile Edit — Revision + Compensating Overwrite).

Responsabilidad (§14 del ticket original, ampliada por Phase 3.2 §2/§4/§9
y Phase 3.5 §13): `list_profiles()` + `create_profile(...)` +
`update_profile(...)` + `filter_profiles(...)` — este service es el ÚNICO
lugar que conoce reglas de negocio de Profile (campos requeridos, el enum
de `source`, QUÉ campos son buscables, CÓMO se normaliza texto para
búsqueda/duplicados, el contrato de duplicate advisory, y ahora la
validación/shaping compartida entre alta y edición — ver Legacy Profile
Contract, Phase 2 §5/§6, Phase 3.2 §3/§9 y Phase 3.5 §13) — el router
NUNCA valida, NUNCA filtra, NUNCA genera IDs, NUNCA serializa JSON, NUNCA
compara `revision` ni loquea; el repository NUNCA valida negocio ni
filtra, solo persiste/lista/actualiza lo que ya llega shapeado, y es
DUEÑO exclusivo de la comparación de `revision`, el lock, y la
compensación de rollback (Phase 3.5 §13: "Service owns validation/
shaping. Repository owns revision comparison/locking/persistence/
rollback" — división estricta, nunca mezclada).

Validación → `ValueError` (el router lo traduce al contrato de error
`Validación` del Hub, re-mostrando el form — MISMO patrón exacto que
`wizard.py::tree_feedback_submit` con `store.submit_feedback`). Fallo de
persistencia → `profile_repository.ProfileStorageError`/
`ProfileConfigIncompleteError` se dejan propagar tal cual (el router las
traduce a los contratos `No disponible`/SOURCE_ERROR y CONFIG_INCOMPLETE
respectivamente, Phase 3.1 §5) — este service no las captura ni las
reinterpreta, para no esconder ninguna de las distinciones que el
repository ya establece. Duplicate advisory → `ProfileDuplicateAdvisory`
(Phase 3.2 §9/§22): una situación semántica propia, nunca un
`ProfileStorageError` ni un `ValueError` — no es un fallo, es una pausa
deliberada que el router traduce a su propio contrato `possible_duplicate`,
nunca reutilizando `No disponible`/`feedback-error`. `update_profile()`
además puede levantar `ProfileNotFoundError`/`ProfileConcurrentModificationError`/
`ProfileRemoteOutcomeUnknownError` (Phase 3.5 §10/§17), todas re-exportadas
tal cual desde `profile_repository` — este service tampoco las reinterpreta.

SIN índice/cache (Phase 3.2 §14, explícito, mismo criterio que Phase 3
§12 para `profile_repository.list()`): `filter_profiles()` recorre en
memoria la lista YA leída por el caller — nunca vuelve a tocar disco, y
`create_profile()`/`update_profile()` solo relee vía
`profile_repository.list()` cuando efectivamente necesita chequear
duplicados (nunca si `duplicate_confirmed=True`, ver abajo)."""
from __future__ import annotations

import logging
from datetime import datetime

from webapp.services import human_capital_catalog_repository as catalog_repo
from webapp.services import busqueda_repository
from webapp.services import entrevista_repository
from webapp.services import profile_repository
from webapp.services.profile_repository import (  # re-exportados para el router
    ProfileConcurrentModificationError,
    ProfileConfigIncompleteError,
    ProfileIdConflictError,
    ProfileNotFoundError,
    ProfileRemoteOutcomeUnknownError,
    ProfileStorageError,
)

__all__ = [
    "ProfileConcurrentModificationError", "ProfileConfigIncompleteError",
    "ProfileDuplicateAdvisory", "ProfileIdConflictError", "ProfileNotFoundError",
    "ProfileRemoteOutcomeUnknownError", "ProfileStorageError",
    "list_profiles", "get_profile", "create_profile", "update_profile", "soft_delete_profile", "filter_profiles",
    "validate_and_shape_fields", "find_duplicate", "create_profile_for_import",
    "list_busquedas", "get_busqueda", "create_busqueda", "update_busqueda",
    "add_candidate_to_busqueda", "move_application_stage", "remove_application", "close_busqueda",
    "profiles_available_for_busqueda", "busqueda_candidates_view",
    "validate_source_for_import",
    "list_entrevistas", "get_entrevista", "create_entrevista_manual", "update_entrevista",
    "marcar_entrevista_no_concretada", "delete_entrevista", "entrevista_view", "list_entrevistas_view",
]

_logger = logging.getLogger("danwor.human_capital")

_REQUIRED_FIELDS = ("first_name", "last_name")
_OPTIONAL_FIELDS = (
    "email", "phone", "linkedin_url", "location", "profile_skills",
    "seniority", "source", "tags", "notes", "potential_rating", "potential_mark",
)

# Campos de texto libre considerados por `q` (Phase 3.2 §3). `linkedin_url`
# DELIBERADAMENTE afuera: es un slug de URL opaco
# (`linkedin.com/in/juan-perez-83a2`) — cualquier señal real de identidad
# que aportaría ya está cubierta por first_name/last_name; incluirlo solo
# suma ruido de substrings de URL, nunca señal de búsqueda nueva. Nunca
# busca dentro de un CV — no existe CV en esta fase (§3, explícito).
_SEARCH_TEXT_FIELDS = ("first_name", "last_name", "email", "profile_skills", "location", "tags", "notes")

# Filtros estructurados con match EXACTO (normalizado) — faceta/categoría
# con catálogo GOBERNADO server-side real (Phase 3.2 §5 / CCHH Catalog
# Foundation §6). `potential_mark` se sumó acá cuando pasó a ser un
# catálogo gobernado (`potencial`, ver P2/HANDOFF) -- el UI ahora lo ofrece
# como `<select>` de opciones fijas, MISMO criterio que seniority/source
# (antes vivía en el grupo CONTAINS de abajo, cuando todavía era un
# `<input type="text">` libre). `location` sigue siendo el único texto
# libre real acá.
_EXACT_MATCH_FILTER_FIELDS = ("seniority", "source", "potential_mark")


class ProfileDuplicateAdvisory(Exception):
    """Posible duplicado detectado durante el alta (Phase 3.2 §9/§12) — NO
    es un fallo de validación (`ValueError`) NI de persistencia
    (`ProfileStorageError`): la creación simplemente todavía no ocurrió,
    a la espera de una decisión explícita del usuario (§10/§11). Lleva el
    Profile EXISTENTE que matcheó (`matched_profile`) para que el router
    tenga con qué identificarlo en el advisory. `duplicate advisory !=
    uniqueness constraint` (§12): esto NUNCA bloquea — `create_profile(...,
    duplicate_confirmed=True)` evita el chequeo por completo, sin ningún
    framework de override/idempotencia genérico detrás."""

    def __init__(self, matched_profile: dict):
        self.matched_profile = matched_profile
        super().__init__("Ya existe un posible perfil duplicado.")


def _normalize_text(value) -> str:
    """Normalización mínima (Phase 3.2 §4), compartida por búsqueda Y
    duplicados — NUNCA dos criterios distintos para el mismo concepto:
    minúsculas + trim + whitespace interno colapsado a un único espacio
    (`" ".join(str(value).lower().split())` colapsa CUALQUIER run de
    espacios/tabs/newlines, no solo espacios simples). Sin librería
    externa — alcanza con `str.split()`/`str.join()`. `None`/`""` ->
    `""` (nunca revienta con valores opcionales ausentes, que son la
    mayoría de los campos de Profile)."""
    if not value:
        return ""
    return " ".join(str(value).lower().split())


def _normalize_email(value) -> str:
    """Un email no tiene whitespace interno legítimo — `_normalize_text`
    (minúsculas + trim) alcanza, sin normalización adicional de
    plus-addressing/punycode/etc. que el legacy tampoco tenía."""
    return _normalize_text(value)


def _matches_search_text(profile: dict, q_normalized: str) -> bool:
    """`q_normalized` vacío -> matchea todo (passthrough). Además de cada
    campo de `_SEARCH_TEXT_FIELDS` por separado, arma también
    `"{first_name} {last_name}"` como un campo más del haystack — así una
    búsqueda por el nombre completo con espacio (p. ej. `"Juan Pérez"`)
    matchea aunque NINGÚN campo individual contenga el string completo
    (§19, caso `first + last name`, distinto de probar cada campo
    suelto)."""
    if not q_normalized:
        return True
    first = profile.get("first_name") or ""
    last = profile.get("last_name") or ""
    haystack_values = [profile.get(field) for field in _SEARCH_TEXT_FIELDS]
    haystack_values.append(f"{first} {last}")
    haystack = " ".join(_normalize_text(value) for value in haystack_values if value)
    return q_normalized in haystack


def _matches_structured_filters(profile: dict, query: dict) -> bool:
    """AND entre TODOS los filtros presentes (§20 — nunca OR salvo que el
    contrato lo pida, y acá no lo pide). Un filtro con valor vacío/ausente
    en `query` no restringe nada (passthrough para ese campo).

    `seniority`/`source`/`potential_mark`: EXACT match normalizado —
    faceta/categoría con catálogo GOBERNADO server-side real (§5 / CCHH
    Catalog Foundation §6 / P2 owner corrective para `potencial`): el UI
    los ofrece como `<select>` de opciones fijas, así que un exact match
    tiene sentido (el usuario elige un valor del catálogo, nunca tipea uno
    parcial). `potential_mark` pasó de CONTAINS a EXACT en este corrective
    -- mientras fue `<input type="text">` libre, CONTAINS era lo correcto
    (ver historial de este archivo); ahora que es un `<select>` gobernado,
    EXACT es lo consistente con seniority/source, MISMO criterio.

    `location`: CONTAINS, no exact match (Phase 3.2 §5, decisión
    explícita) — el único filtro de texto libre real que queda, tipeado a
    mano sin lista fija (un exact match sobre texto libre lo volvería casi
    inútil: "Buenos Aires"/"CABA"/"Bs As" no matchean entre sí). MISMO
    tratamiento que ya le da la búsqueda textual libre (`q`)."""
    for field in _EXACT_MATCH_FILTER_FIELDS:
        wanted = _normalize_text(query.get(field))
        if wanted and _normalize_text(profile.get(field)) != wanted:
            return False
    wanted_location = _normalize_text(query.get("location"))
    if wanted_location and wanted_location not in _normalize_text(profile.get("location")):
        return False
    return True


def filter_profiles(profiles: list[dict], query: dict | None) -> list[dict]:
    """Filtrado EN MEMORIA sobre una lista YA leída por el caller (Phase
    3.2 §14/§6): sin índice/cache, sin I/O propio — el router llama
    `list_profiles()` una única vez y pasa el resultado acá. `query`
    vacío/`None` -> passthrough (una copia de `profiles`, nunca un `[]`
    espurio). Combina `q` (texto libre) y los filtros estructurados con
    AND (§20)."""
    if not query:
        return list(profiles)
    q_normalized = _normalize_text(query.get("q"))
    return [
        profile for profile in profiles
        if _matches_search_text(profile, q_normalized) and _matches_structured_filters(profile, query)
    ]


def find_duplicate(profile: dict, existing: list[dict], *, exclude_profile_id: str | None = None) -> dict | None:
    """Contrato EXACTO de duplicate advisory pedido por el ticket (Phase
    3.2 §9 — el ZIP legacy original no está disponible en este entorno/
    sesión, ver OUTPUT §1: se reconstruye tal cual el algoritmo dado
    verbatim en el ticket, ya descripto ahí como previamente probado):

      1. si el Profile nuevo trae `email`: matchea por email normalizado
         contra los `existing` — SOLO por email, el nombre no participa
         en absoluto en esta rama (esto es lo que hace que "mismo nombre,
         emails explícitos DISTINTOS" -> NO sea advisory, §21: al haber
         email en la entrada nueva, el algoritmo nunca cae a comparar
         nombres).
      2. si NO trae `email`: matchea por `first_name`+`last_name`
         normalizados contra los `existing` (sin importar si el
         `existing` en sí tiene o no email cargado).

    Devuelve el PRIMER match o `None` — nunca levanta nada, es el caller
    (`create_profile`/`update_profile`) quien decide qué hacer con el
    resultado. Advisory, NUNCA uniqueness constraint (§12): la app puede
    legítimamente contener personas con el mismo nombre, emails
    faltantes, o incluso duplicados deliberados — esta función solo
    informa, nunca bloquea.

    `exclude_profile_id` (Phase 3.5 §17): al editar un Profile existente,
    ese MISMO Profile (con su email/nombre actuales, todavía sin la
    edición aplicada) está en `existing` — sin excluirlo, cualquier edición
    que no cambie email/nombre se marcaría como "duplicado de sí mismo".
    `create_profile` nunca pasa este parámetro (no hay profile_id propio
    todavía que excluir).

    PÚBLICA desde Phase 3.8: `profile_import_service.py` reusa esta MISMA
    función tal cual para detectar duplicados DENTRO del archivo subido
    (mismo algoritmo — `existing` pasa a ser la lista de filas ya
    aceptadas del MISMO archivo en vez de Profiles persistidos — Phase
    3.7 §8/§10, "no bulk duplicate detection v2")."""
    if exclude_profile_id is not None:
        existing = [p for p in existing if p.get("profile_id") != exclude_profile_id]
    email = _normalize_email(profile.get("email"))
    if email:
        for candidate in existing:
            if _normalize_email(candidate.get("email")) == email:
                return candidate
        return None
    first = _normalize_text(profile.get("first_name"))
    last = _normalize_text(profile.get("last_name"))
    for candidate in existing:
        if _normalize_text(candidate.get("first_name")) == first and _normalize_text(candidate.get("last_name")) == last:
            return candidate
    return None


def list_profiles() -> list[dict]:
    # Excluye los borrados lógicos (owner closure: un candidato soft-deleted
    # NO aparece en listados/búsquedas normales). El dedup de create/update
    # sigue mirando `profile_repository.list()` crudo a propósito (un borrado
    # no debería habilitar re-crear un duplicado silenciosamente).
    return [p for p in profile_repository.list() if not p.get("deleted_at")]


def soft_delete_profile(profile_id: str, actor: str | None = None) -> dict:
    """Borrado LÓGICO del candidato (owner closure). Una sola operación de
    servicio, compartida por los dos puntos de entrada de UI (listado y
    edición). Documentos preservados. Delega en `profile_repository.soft_delete`
    (durabilidad remota + idempotencia)."""
    return profile_repository.soft_delete(profile_id, actor)


def get_profile(profile_id: str) -> dict | None:
    """Delegación directa a `profile_repository.get` (Phase 3.5 §14) —
    usada por el router para poblar el form de edición y para el chequeo
    de existencia (404) ANTES de intentar `update_profile`. `None` si no
    existe, MISMO criterio fail-soft del repository."""
    return profile_repository.get(profile_id)


def validate_and_shape_fields(fields: dict) -> dict:
    """Validación + shaping de negocio COMPARTIDA entre `create_profile`/
    `update_profile`/`profile_import_service.py` (Phase 3.5 §13: "Service
    owns Profile input validation; domain shaping" — un solo lugar, nunca
    duplicado entre alta manual, edición, e import masivo — Phase 3.8 §8:
    "Do NOT implement a parallel bulk validator"). `fields` es el input
    crudo (strings) — sea de un form HTML o de una fila de spreadsheet ya
    convertida a texto por el parser de import.
    Levanta `ValueError` (mensaje listo para mostrar al usuario) si:
      - falta `first_name`/`last_name` (§3, únicos campos requeridos —
        `email` sigue siendo opcional, nunca se usa como identidad, ver
        Phase 2 §5/§8);
      - `potential_rating` no es un entero 1-5 (MISMO rango que el CHECK
        legacy `banco_cvs.potencial_estrellas`, ver Phase 2 §5).

    `seniority`/`source`/`potential_mark` NO se validan acá (CCHH Catalog
    Foundation §6 / P2): son catálogos GOBERNADOS con contrato
    migration-safe CREATE/EDIT distinto (ver
    `_validate_seniority_governed`/`_validate_source_governed`/
    `_validate_potencial_governed`, llamadas solo desde
    `create_profile`/`update_profile`, nunca desde acá -- esta función
    también la usa el import masivo, que deliberadamente NO gobierna
    ninguno de los tres, mismo criterio para los tres campos).

    Devuelve el dict de campos de NEGOCIO ya shapeado (sin `profile_id`/
    `schema_version`/`revision`/timestamps — esos son responsabilidad
    exclusiva del repository, tanto en `create()` como en `update()`)."""
    first_name = (fields.get("first_name") or "").strip()
    last_name = (fields.get("last_name") or "").strip()
    if not first_name or not last_name:
        raise ValueError("Completá nombre y apellido.")

    source = (fields.get("source") or "").strip() or None

    potential_rating_raw = (fields.get("potential_rating") or "").strip()
    potential_rating: int | None = None
    if potential_rating_raw:
        try:
            potential_rating = int(potential_rating_raw)
        except ValueError:
            raise ValueError("El potencial en estrellas tiene que ser un número entre 1 y 5.") from None
        if not (1 <= potential_rating <= 5):
            raise ValueError("El potencial en estrellas tiene que estar entre 1 y 5.")

    return {
        "first_name": first_name,
        "last_name": last_name,
        "email": (fields.get("email") or "").strip() or None,
        "phone": (fields.get("phone") or "").strip() or None,
        "linkedin_url": (fields.get("linkedin_url") or "").strip() or None,
        "location": (fields.get("location") or "").strip() or None,
        "profile_skills": (fields.get("profile_skills") or "").strip() or None,
        "seniority": (fields.get("seniority") or "").strip() or None,
        "source": source,
        "tags": (fields.get("tags") or "").strip() or None,
        "notes": (fields.get("notes") or "").strip() or None,
        "potential_rating": potential_rating,
        "potential_mark": (fields.get("potential_mark") or "").strip() or None,
    }


def _is_governed_value_allowed(catalog_name: str, new_value: str | None, *, existing_value: str | None) -> bool:
    """Lógica COMPARTIDA migration-safe (CCHH Catalog Foundation §6) detrás
    de todo catálogo gobernado -- Seniority y Fuente aplican EXACTAMENTE el
    mismo contrato, sólo cambia el catálogo consultado y el mensaje que cada
    caller arma con el resultado:
      - CREATE (`existing_value is None`): `new_value` vacío es válido
        (campo opcional); cualquier valor NO vacío DEBE pertenecer al
        catálogo ACTIVO — sin fallback a texto libre aunque el snapshot no
        esté disponible (en ese caso el único valor aceptable es vacío).
      - EDIT (`existing_value` provisto): si `new_value == existing_value`
        el valor legacy fuera-de-catálogo se PRESERVA (una edición no
        relacionada sigue siendo posible); si CAMBIA, el nuevo valor debe
        pertenecer al catálogo activo (o ser vacío).
    Nunca agrega el valor legacy al catálogo automáticamente."""
    if new_value is None:
        return True
    if existing_value is not None and new_value == existing_value:
        return True  # legacy preservado (edición no relacionada)
    return new_value in catalog_repo.catalog_values(catalog_name)


def _validate_seniority_governed(new_value: str | None, *, existing_value: str | None) -> None:
    if _is_governed_value_allowed("seniority", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("seniority")
    if active:
        raise ValueError(f"Seniority inválido. Elegí uno del catálogo vigente: {', '.join(active)}.")
    raise ValueError(
        "El catálogo de Seniority todavía no está disponible. Dejá el campo vacío "
        "o refrescá el catálogo antes de asignar un valor."
    )


def _validate_source_governed(new_value: str | None, *, existing_value: str | None) -> None:
    if _is_governed_value_allowed("fuente", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("fuente")
    if active:
        raise ValueError(f"Fuente inválida. Elegí una del catálogo vigente: {', '.join(active)}.")
    raise ValueError(
        "El catálogo de Fuente todavía no está disponible. Dejá el campo vacío "
        "o refrescá el catálogo antes de asignar un valor."
    )


def _validate_potencial_governed(new_value: str | None, *, existing_value: str | None) -> None:
    if _is_governed_value_allowed("potencial", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("potencial")
    if active:
        raise ValueError(f"Marca de potencial inválida. Elegí una del catálogo vigente: {', '.join(active)}.")
    raise ValueError(
        "El catálogo de Marca de potencial todavía no está disponible. Dejá el campo vacío "
        "o refrescá el catálogo antes de asignar un valor."
    )


def _validate_area_governed(new_value: str | None, *, existing_value: str | None) -> None:
    """Área / Departamento de una Búsqueda: gobernada por el catálogo `area`
    (en la app de referencia el select sale de Configuración, no de una lista
    hardcodeada). MISMO contrato migration-safe que Seniority/Fuente."""
    if _is_governed_value_allowed("area", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("area")
    if active:
        raise ValueError(f"Área / Departamento inválida. Elegí una del catálogo vigente: {', '.join(active)}.")
    raise ValueError(
        "El catálogo de Áreas todavía no está disponible. Dejá el campo vacío "
        "o refrescá el catálogo antes de asignar un valor."
    )


def _validate_area_consultora_governed(new_value: str | None, *, existing_value: str | None) -> None:
    """Área de la consultora responsable de la Búsqueda: gobernada por el
    catálogo `area_consultora` (mismo catálogo que usa el Banco). MISMO
    contrato migration-safe que Seniority/Fuente."""
    if _is_governed_value_allowed("area_consultora", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("area_consultora")
    if active:
        raise ValueError(f"Área de la consultora inválida. Elegí una del catálogo vigente: {', '.join(active)}.")
    raise ValueError(
        "El catálogo de Áreas de la consultora todavía no está disponible. Dejá el campo vacío "
        "o refrescá el catálogo antes de asignar un valor."
    )


def _validate_modalidad_contratacion_governed(new_value: str | None, *, existing_value: str | None) -> None:
    """Modalidad de contratación de una Entrevista (Full time/Part time en la
    referencia): gobernada por el catálogo `modalidad_contratacion` -- MISMO
    contrato migration-safe que Seniority/Fuente/Área. Catálogo ya existía
    (`GOVERNED_CATALOGS`) pero no tenía consumidor todavía; Entrevistas es el
    primero."""
    if _is_governed_value_allowed("modalidad_contratacion", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("modalidad_contratacion")
    if active:
        raise ValueError(f"Modalidad de contratación inválida. Elegí una del catálogo vigente: {', '.join(active)}.")
    raise ValueError(
        "El catálogo de Modalidad de contratación todavía no está disponible. Dejá el campo vacío "
        "o refrescá el catálogo antes de asignar un valor."
    )


def validate_source_for_import(source: str | None) -> None:
    """Import masivo (Phase 3.8 §8: "sin validador bulk paralelo", ver
    `profile_import_service.py`, único caller) NO pasa por `create_profile`
    y por lo tanto nunca corre `_validate_source_governed` -- pero ANTES de
    este catálogo, `validate_and_shape_fields` (compartida con import) SÍ
    rechazaba una fuente fuera de `VALID_SOURCES` (Legacy Profile Contract,
    Phase 2 §5/§6). Preservar ese comportamiento (CCHH Catalog Foundation,
    residual decision de Fuente: "no cambiar semántica todavía" del import)
    exige la MISMA regla que CREATE -- nunca la escapatoria migration-safe
    de EDIT, que no aplica acá: una fila de import es SIEMPRE un alta
    nueva, nunca tiene un valor legacy previo que preservar. MISMA
    autoridad que create/edit (el catálogo `fuente`), nunca una lista
    propia -- por eso esto delega en `_validate_source_governed` en vez de
    reimplementar la regla."""
    _validate_source_governed(source, existing_value=None)


def create_profile(fields: dict, *, duplicate_confirmed: bool = False) -> dict:
    """Validación SIEMPRE corre primero — un chequeo de duplicados sobre un
    Profile todavía inválido no tendría sentido (Phase 3.2).

    `duplicate_confirmed=False` (default): si `find_duplicate` encuentra
    un match contra los Profiles YA existentes (`profile_repository.list()`
    — una lectura adicional, solo acá, nunca si ya viene confirmado),
    levanta `ProfileDuplicateAdvisory` con el Profile existente que
    matcheó — la creación NO ocurre todavía (§9/§10). `duplicate_confirmed
    =True` (Phase 3.2 §11 — override explícito y server-side, nunca un
    framework de idempotencia/estado global): salta el chequeo por
    completo, ni siquiera relee el listado — el usuario ya decidió
    continuar deliberadamente.

    Devuelve el Profile YA persistido (incluye `profile_id`/`schema_version`/
    `revision`/`created_at`/`updated_at`, agregados por el repository)."""
    profile = validate_and_shape_fields(fields)
    _validate_seniority_governed(profile.get("seniority"), existing_value=None)
    _validate_source_governed(profile.get("source"), existing_value=None)
    _validate_potencial_governed(profile.get("potential_mark"), existing_value=None)
    if not duplicate_confirmed:
        duplicate = find_duplicate(profile, profile_repository.list())
        if duplicate is not None:
            raise ProfileDuplicateAdvisory(duplicate)
    return profile_repository.create(profile)


def update_profile(
    profile_id: str, expected_revision: int, fields: dict, *, duplicate_confirmed: bool = False,
) -> dict:
    """Edición de un Profile EXISTENTE (Phase 3.5). MISMA validación que
    `create_profile` (`validate_and_shape_fields`, un solo lugar) —
    corre SIEMPRE antes de cualquier chequeo de duplicado o de
    concurrencia, para no gastar un lock/lectura sobre un candidato
    todavía inválido.

    Duplicate advisory (Phase 3.5 §17): una edición puede cambiar
    email/nombre y por lo tanto crear un duplicado nuevo que no existía
    antes de la edición — se corre el MISMO `find_duplicate` que usa
    `create_profile`, mismo criterio, EXCLUYENDO este mismo `profile_id`
    del conjunto de comparación (`exclude_profile_id`) para que una
    edición que no cambia email/nombre nunca se marque como duplicado de
    sí misma. `duplicate_confirmed=True` salta el chequeo exactamente
    igual que en `create_profile`.

    Concurrencia/persistencia (`revision` comparison, lock, compensating
    overwrite) es responsabilidad EXCLUSIVA de `profile_repository.update`
    (Phase 3.5 §13) — este service nunca compara `revision` ni loquea,
    solo pasa `expected_revision` tal cual llegó del caller. Las
    excepciones de esa capa (`ProfileNotFoundError`/
    `ProfileConcurrentModificationError`/`ProfileConfigIncompleteError`/
    `ProfileStorageError`/`ProfileRemoteOutcomeUnknownError`) se dejan
    propagar sin reinterpretar, MISMO criterio que `create_profile` con
    las suyas."""
    profile = validate_and_shape_fields(fields)
    _existing = profile_repository.get(profile_id)
    if _existing is not None:
        # Si no existe, se deja que profile_repository.update levante NotFound
        # (no enmascararlo con un error de catálogo). El `or ""` normaliza el
        # legacy ausente a "" para comparar contra el nuevo valor shapeado.
        _validate_seniority_governed(
            profile.get("seniority"),
            existing_value=(_existing.get("seniority") or None),
        )
        _validate_source_governed(
            profile.get("source"),
            existing_value=(_existing.get("source") or None),
        )
        _validate_potencial_governed(
            profile.get("potential_mark"),
            existing_value=(_existing.get("potential_mark") or None),
        )
    if not duplicate_confirmed:
        duplicate = find_duplicate(profile, profile_repository.list(), exclude_profile_id=profile_id)
        if duplicate is not None:
            raise ProfileDuplicateAdvisory(duplicate)
    return profile_repository.update(profile_id, expected_revision, profile)


def create_profile_for_import(profile_id: str, fields: dict) -> tuple[dict, bool]:
    """Camino INTERNO de bulk import (Phase 3.8 §17) — NUNCA expuesto al
    alta manual, nombre deliberadamente DISTINTO de `create_profile` para
    que una identidad preasignada externa nunca se confunda con una
    feature general del alta. Solo `profile_import_service.py` debe
    llamar esto, y solo DESPUÉS de verificar la firma del token de fila
    (Phase 3.8 §13) — este service confía en que `profile_id`/`fields`
    YA pasaron esa verificación; no vuelve a validar negocio ni a
    chequear duplicados acá (esas decisiones ya se tomaron en preview,
    con el listado completo de Profiles existentes disponible en ese
    momento — repetirlas acá sería una segunda pasada de negocio
    redundante, no una confirmación de persistencia).

    Delega la comparación de idempotencia/collision íntegramente a
    `profile_repository.create_with_explicit_id` (Phase 3.8 §14/§15) —
    devuelve `(profile, created)`: `created=True` es CREATED,
    `created=False` es ALREADY_COMMITTED (retry idempotente de la MISMA
    fila, cero rewrite). `ProfileIdConflictError`/
    `ProfileConfigIncompleteError`/`ProfileStorageError`/
    `ProfileRemoteOutcomeUnknownError` se dejan propagar sin
    reinterpretar, MISMO criterio que el resto de este service."""
    return profile_repository.create_with_explicit_id(profile_id, fields)


# ═══════════════════════════════════════════════════════════════════════════
# BÚSQUEDAS (procesos de selección) — validación/shaping + delegación al
# `busqueda_repository`. MISMA división que Profile: el service es DUEÑO de las
# reglas de negocio (campos requeridos, enums de dominio, estado derivado del
# candidato); el repository sólo persiste/lista/actualiza lo ya shapeado y es
# dueño de revision/lock/rollback. La relación con candidatos usa `profile_id`.
# ═══════════════════════════════════════════════════════════════════════════

def list_busquedas() -> list[dict]:
    return busqueda_repository.list()


def get_busqueda(busqueda_id: str) -> dict | None:
    return busqueda_repository.get(busqueda_id)


def _shape_busqueda_fields(fields: dict, *, existing: dict | None = None) -> dict:
    """Valida y shapea los campos de una búsqueda (alta/edición). `nombre_puesto`
    y `fecha_apertura` son obligatorios (MISMO contrato que la referencia).
    `modalidad` es un enum de dominio fijo (Híbrido/Remoto, no es catálogo).
    `area_departamento` y `area_consultora` son GOBERNADAS por catálogo (`area`
    y `area_consultora`) con el mismo contrato migration-safe que Seniority: en
    edición, un valor legacy fuera-de-catálogo se preserva si no cambia."""
    nombre = (fields.get("nombre_puesto") or "").strip()
    if not nombre:
        raise ValueError("Completá el nombre del puesto.")
    fecha = (fields.get("fecha_apertura") or "").strip()
    if not fecha:
        raise ValueError("Completá la fecha de apertura.")
    modalidad = (fields.get("modalidad") or "").strip() or None
    if modalidad is not None and modalidad not in busqueda_repository.MODALIDADES:
        raise ValueError("Modalidad inválida.")
    area_dep = (fields.get("area_departamento") or "").strip() or None
    _validate_area_governed(area_dep, existing_value=(existing or {}).get("area_departamento"))
    area_cons = (fields.get("area_consultora") or "").strip() or None
    _validate_area_consultora_governed(area_cons, existing_value=(existing or {}).get("area_consultora"))
    return {
        "nombre_puesto": nombre,
        "fecha_apertura": fecha,
        "descripcion": (fields.get("descripcion") or "").strip() or None,
        "area_departamento": area_dep,
        "modalidad": modalidad,
        "area_consultora": area_cons,
        "pedido_formal_recibido": bool(fields.get("pedido_formal_recibido")),
        "jd_texto": (fields.get("jd_texto") or "").strip() or None,
        "palabras_clave": (fields.get("palabras_clave") or "").strip() or None,
    }


def create_busqueda(fields: dict) -> dict:
    return busqueda_repository.create(_shape_busqueda_fields(fields))


def update_busqueda(busqueda_id: str, expected_revision: int, fields: dict) -> dict:
    existing = busqueda_repository.get(busqueda_id)
    return busqueda_repository.update(
        busqueda_id, expected_revision, _shape_busqueda_fields(fields, existing=existing))


def close_busqueda(busqueda_id: str, finalize_application_ids: list[str] | None = None) -> dict:
    return busqueda_repository.close(busqueda_id, finalize_application_ids)


def add_candidate_to_busqueda(busqueda_id: str, profile_id: str) -> dict:
    profile = profile_repository.get(profile_id)
    if profile is None or profile.get("deleted_at"):
        raise ValueError("El candidato no existe o fue eliminado del Banco de CVs.")
    return busqueda_repository.add_candidate(busqueda_id, profile_id)


def remove_application(busqueda_id: str, application_id: str) -> dict:
    return busqueda_repository.remove_candidate(busqueda_id, application_id)


# Etapas que implican que "hubo entrevista" -- MISMO gate que
# actualizarEtapaPipeline en la referencia (lib/actions/candidatos.ts):
# "cualquier etapa posterior a la preselección implica que hubo entrevista
# CCHH: si todavía no está registrada (por ejemplo, se pasó directo a
# Entrevista Líder), se crea igual". Verbatim, sin inventar.
_ETAPAS_QUE_DISPARAN_ENTREVISTA = ("Entrevista CCHH", "Entrevista Líder", "Decisión Final")


def move_application_stage(busqueda_id: str, application_id: str, etapa: str,
                           resultado_final: str | None) -> dict:
    if etapa not in busqueda_repository.ETAPAS:
        raise ValueError("Etapa inválida.")
    resultado = (resultado_final or "").strip() or None
    if etapa == busqueda_repository.ETAPA_FINAL:
        if resultado is not None and resultado not in busqueda_repository.RESULTADOS_FINALES:
            raise ValueError("Resultado final inválido.")
    else:
        resultado = None  # el repo también lo fuerza; se valida acá el contrato
    busqueda = busqueda_repository.move_stage(busqueda_id, application_id, etapa, resultado)
    if etapa in _ETAPAS_QUE_DISPARAN_ENTREVISTA:
        app = next((a for a in busqueda.get("candidatos", []) if a.get("application_id") == application_id), None)
        if app is not None:
            crear_entrevista_automatica_si_no_existe(app["profile_id"], busqueda_id, application_id)
    return busqueda


def profiles_available_for_busqueda(busqueda: dict) -> list[dict]:
    """Profiles del Banco ASIGNABLES a ESTA búsqueda (contrato N:M aprobado por
    el owner: un Profile puede participar en 0..N búsquedas, incluidas varias
    activas a la vez). Se excluye ÚNICAMENTE a quien ya tiene una postulación
    NO finalizada en ESTA misma búsqueda — nunca por participar en otra. Un
    "En proceso" derivado de otra búsqueda NO es una restricción de
    exclusividad. Excluye borrados (via `list_profiles`).

    (La app de referencia SÍ enforcea membership única global — banco_cvs.estado
    'Nuevo' como único asignable, ver AgregarDesdeBancoModal.tsx:111/143-145 —
    pero el contrato de dominio aprobado para el Hub es N:M, y prevalece.)"""
    in_this_search = {
        app.get("profile_id") for app in busqueda.get("candidatos", [])
        if app.get("etapa") != busqueda_repository.ETAPA_FINAL
    }
    return [p for p in list_profiles() if p["profile_id"] not in in_this_search]


def busqueda_candidates_view(busqueda: dict) -> list[dict]:
    """Enriquece cada postulación embebida con los datos del Profile del Banco
    (join por `profile_id`) para presentarlos — el Banco sigue siendo el maestro
    de los datos personales, acá no se copian, se leen en vivo. `profile` puede
    ser None si el candidato fue borrado del Banco."""
    out = []
    for app in busqueda.get("candidatos", []):
        out.append({**app, "profile": profile_repository.get(app.get("profile_id"))})
    return out


# ── Señales derivadas de atención (NO son estados estructurales) ──────────────
# Un proceso vive UNA sola vez en su contenedor de lifecycle (activo/cerrado).
# "Estancada" / "sin requerimiento" son CONDICIONES DERIVADAS que se expresan con
# badges en el tile + filtros — NUNCA duplicando el proceso en otro bloque (mejora
# deliberada sobre la app de referencia, que sí lo duplicaba). La regla de
# "estancada" es la MISMA de la referencia (lib/metricas.busquedasEstancadas):
# 30+ días sin que NINGÚN candidato cambie de etapa.
BUSQUEDA_UMBRAL_ESTANCADA_DIAS = 30


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("Z", "")
    for cut in (19, 10):
        try:
            return datetime.fromisoformat(s[:cut])
        except ValueError:
            continue
    return None


def busqueda_dias_sin_movimiento(busqueda: dict) -> int | None:
    """Días desde el ÚLTIMO movimiento del proceso = el más reciente entre los
    `fecha_cambio_etapa` (o `created_at`) de sus candidatos; sin candidatos, la
    `fecha_apertura`. MISMO cálculo que la referencia. None si no hay fecha
    parseable (nunca se asume estancada por falta de dato)."""
    fechas = []
    for c in busqueda.get("candidatos", []):
        dt = _parse_iso_dt(c.get("fecha_cambio_etapa") or c.get("created_at"))
        if dt:
            fechas.append(dt)
    ultimo = max(fechas) if fechas else _parse_iso_dt(busqueda.get("fecha_apertura"))
    if ultimo is None:
        return None
    return max((datetime.now() - ultimo).days, 0)


def busqueda_attention(busqueda: dict) -> dict:
    """Señales derivadas de atención de un proceso, para badges + filtros. Sólo
    presentación/UX — no muta el documento ni cambia su lifecycle. `estancada`
    aplica ÚNICAMENTE a procesos activos (igual que la referencia)."""
    dias = busqueda_dias_sin_movimiento(busqueda)
    activo = busqueda.get("estado") == "activo"
    return {
        "dias_sin_movimiento": dias,
        "estancada": bool(activo and dias is not None and dias >= BUSQUEDA_UMBRAL_ESTANCADA_DIAS),
        "sin_requerimiento": not busqueda.get("pedido_formal_recibido"),
    }


def busqueda_with_attention(busqueda: dict) -> dict:
    """Copia del proceso con `attention` embebido (nunca se persiste)."""
    return {**busqueda, "attention": busqueda_attention(busqueda)}


# ── ENTREVISTAS ───────────────────────────────────────────────────────────────
# Dominio verbatim de la referencia (ver entrevista_repository.py, module
# docstring, para las citas exactas de schema.sql/lib/entrevistaAutomatica.ts).

def list_entrevistas() -> list[dict]:
    return entrevista_repository.list()


def get_entrevista(entrevista_id: str) -> dict | None:
    return entrevista_repository.get(entrevista_id)


def _shape_entrevista_fields(fields: dict, *, existing: dict | None = None) -> dict:
    """Ningún campo de Entrevista es obligatorio (MISMO contrato que la
    referencia: EntrevistaFormInput es enteramente opcional salvo elegir un
    candidato al CREAR, que se valida aparte con profile_id). `area` y
    `modalidad_contratacion` son GOBERNADOS por catálogo, MISMO contrato
    migration-safe que Búsquedas/Candidatos."""
    area = (fields.get("area") or "").strip() or None
    _validate_area_consultora_governed(area, existing_value=(existing or {}).get("area"))
    modalidad = (fields.get("modalidad_contratacion") or "").strip() or None
    _validate_modalidad_contratacion_governed(
        modalidad, existing_value=(existing or {}).get("modalidad_contratacion"))
    return {
        "fecha_entrevista": (fields.get("fecha_entrevista") or "").strip() or None,
        "puesto_evaluado": (fields.get("puesto_evaluado") or "").strip() or None,
        "area": area,
        "modalidad_contratacion": modalidad,
        "remuneracion_pretendida": (fields.get("remuneracion_pretendida") or "").strip() or None,
        "observaciones": (fields.get("observaciones") or "").strip() or None,
        "resumen_entrevista_tecnica": (fields.get("resumen_entrevista_tecnica") or "").strip() or None,
    }


def create_entrevista_manual(profile_id: str, fields: dict) -> dict:
    """Alta manual (candidato del Banco todavía sin ninguna Búsqueda) -- MISMO
    caso que `crearEntrevistaManual` en la referencia (banco_cv_id, sin
    proceso_id/candidato_id)."""
    profile = profile_repository.get(profile_id)
    if profile is None or profile.get("deleted_at"):
        raise ValueError("Elegí un candidato del Banco de CVs.")
    return entrevista_repository.create(
        _shape_entrevista_fields(fields), profile_id=profile_id, busqueda_id=None, application_id=None)


def update_entrevista(entrevista_id: str, expected_revision: int, fields: dict) -> dict:
    existing = entrevista_repository.get(entrevista_id)
    return entrevista_repository.update(
        entrevista_id, expected_revision, _shape_entrevista_fields(fields, existing=existing))


def marcar_entrevista_no_concretada(entrevista_id: str, expected_revision: int) -> dict:
    return entrevista_repository.marcar_no_concretada(entrevista_id, expected_revision)


def delete_entrevista(entrevista_id: str) -> None:
    entrevista_repository.delete(entrevista_id)


def crear_entrevista_automatica_si_no_existe(profile_id: str, busqueda_id: str, application_id: str) -> None:
    """Al entrar a una etapa de entrevista se crea una única fila para ese
    (profile_id, busqueda_id) si todavía no existe -- MISMO gate/dedup que
    `crearEntrevistaAutomaticaSiNoExiste` en la referencia (unique index
    candidato_id+proceso_id). Autocompleta puesto (de la Búsqueda) y área (de
    la Búsqueda, MISMA taxonomía que `area_consultora` -- ver migración de
    referencia 2026-09-24-fase2.sql); el origen NO se autocompleta como campo
    propio porque se deriva siempre del Profile (`entrevista_view`). Todo
    corregible después desde Entrevistas. Best-effort: un fallo de
    persistencia acá NUNCA debe romper el movimiento de etapa que lo disparó
    (mismo criterio que `add_candidate_to_busqueda`/`remove_application` en
    los call sites del router: la mutación de pipeline ya se confirmó)."""
    if entrevista_repository.find_by_profile_and_busqueda(profile_id, busqueda_id) is not None:
        return
    busqueda = busqueda_repository.get(busqueda_id)
    fields = {
        "puesto_evaluado": (busqueda or {}).get("nombre_puesto"),
        "area": (busqueda or {}).get("area_consultora"),
    }
    try:
        entrevista_repository.create(
            _shape_entrevista_fields(fields), profile_id=profile_id,
            busqueda_id=busqueda_id, application_id=application_id,
        )
    except (ValueError, entrevista_repository.EntrevistaStorageError):
        # No debe bloquear el movimiento de etapa (ya confirmado) por un
        # problema de creación de la entrevista automática; queda para
        # cargarse manualmente desde Entrevistas.
        _logger.warning("human_capital.entrevista_automatica_fallo profile_id=%s busqueda_id=%s",
                         profile_id, busqueda_id)


def entrevista_view(entrevista: dict) -> dict:
    """Enriquece una entrevista con Profile (maestro de datos personales +
    `source`, del que se deriva SIEMPRE el origen mostrado -- MISMA prioridad
    que `origenDeEntrevista` de la referencia, pero sin duplicar el campo) y
    Búsqueda (de la que se deriva el puesto evaluado mientras haya
    `busqueda_id`, MISMA prioridad que `puestoDeEntrevista`)."""
    profile = profile_repository.get(entrevista.get("profile_id"))
    busqueda = busqueda_repository.get(entrevista["busqueda_id"]) if entrevista.get("busqueda_id") else None
    puesto = (busqueda or {}).get("nombre_puesto") or entrevista.get("puesto_evaluado")
    return {
        **entrevista,
        "profile": profile,
        "busqueda": busqueda,
        "origen": (profile or {}).get("source"),
        "puesto_mostrado": puesto,
        "puesto_es_derivado": bool(busqueda),
    }


def list_entrevistas_view() -> list[dict]:
    return [entrevista_view(e) for e in list_entrevistas()]
