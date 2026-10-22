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

import copy
import logging
import re
from datetime import date, datetime

from webapp.services import human_capital_catalog_repository as catalog_repo
from webapp.services import busqueda_repository
from webapp.services import entrevista_repository
from webapp.services import onboarding_checklist
from webapp.services import onboarding_repository
from webapp.services import profile_repository
from webapp.services import publicacion_repository
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
    "list_profiles", "get_profile", "create_profile", "update_profile", "soft_delete_profile", "filter_profiles", "profile_search_haystack",
    "validate_and_shape_fields", "find_duplicate", "create_profile_for_import",
    "list_busquedas", "get_busqueda", "create_busqueda", "update_busqueda",
    "add_candidate_to_busqueda", "move_application_stage", "remove_application", "close_busqueda",
    "busqueda_is_placeholder", "delete_busqueda",
    "create_solicitud", "edit_solicitud", "resubmit_solicitud", "cancel_solicitud",
    "observe_solicitud", "take_solicitud", "get_solicitud_view", "list_solicitudes_view", "solicitud_view",
    "list_busquedas_gestionadas", "resumen_solicitudes",
    "profiles_available_for_busqueda", "busqueda_candidates_view",
    "validate_source_for_import",
    "list_entrevistas", "get_entrevista", "create_entrevista_manual", "update_entrevista",
    "marcar_entrevista_no_concretada", "delete_entrevista", "entrevista_view", "list_entrevistas_view",
    "COMPENSATION_MASK", "mask_entrevista_compensation",
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
    return q_normalized in profile_search_haystack(profile)


def profile_search_haystack(profile: dict) -> str:
    """Texto normalizado contra el que se busca un candidato (nombre, email, skills, ubicación, tags, notas y «nombre apellido»). ÚNICA definición: el buscador del landing de Base de CVs
    (`_matches_search_text`) y el del diálogo «Agregar candidato desde el Banco» (filtro en vivo en el navegador, que normaliza la consulta igual que `_normalize_text`) la comparten."""
    first = profile.get("first_name") or ""
    last = profile.get("last_name") or ""
    haystack_values = [profile.get(field) for field in _SEARCH_TEXT_FIELDS]
    haystack_values.append(f"{first} {last}")
    return " ".join(_normalize_text(value) for value in haystack_values if value)


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


def create_busqueda(fields: dict, *, actor: str | None = None) -> dict:
    """Alta directa de CCHH. `actor` (opcional hasta que el router lo pase) queda
    en `origen.creado_por`; el `origen` se identifica SIEMPRE."""
    return busqueda_repository.create(_shape_busqueda_fields(fields), actor=(actor or "").strip().lower() or None)


def update_busqueda(busqueda_id: str, expected_revision: int, fields: dict) -> dict:
    existing = busqueda_repository.get(busqueda_id)
    return busqueda_repository.update(
        busqueda_id, expected_revision, _shape_busqueda_fields(fields, existing=existing))


def _candidate_display_name(profile_id) -> str | None:
    """Nombre visible del candidato para la FOTO del historial del proceso (nunca bloquea un movimiento)."""
    profile = profile_repository.get(profile_id) if profile_id else None
    if not profile:
        return None
    return " ".join(p for p in (str(profile.get("first_name") or "").strip(), str(profile.get("last_name") or "").strip()) if p) or None


def close_busqueda(busqueda_id: str, finalize_application_ids: list[str] | None = None, *, actor: str | None = None) -> dict:
    busqueda = busqueda_repository.close(busqueda_id, finalize_application_ids, actor=actor, names=_candidate_display_name)
    sync_onboardings_for_busqueda(busqueda)             # misma condición central tras CUALQUIER mutación de candidaturas
    return busqueda


def busqueda_is_placeholder(busqueda: dict) -> bool:
    """`True` sii esta búsqueda es un placeholder/dummy sin ningún dato real
    vinculado todavía -- owner request explícito (diálogo "Cerrar proceso"):
    "sólo es posible eliminar si fue un placeholder/dummy sin datos
    vinculados. Un proceso iniciado no puede eliminarse". Dos condiciones,
    AMBAS deben cumplirse:
      - cero postulaciones embebidas (`candidatos`) -- nadie fue agregado
        todavía al pipeline;
      - cero Entrevistas que referencien este `busqueda_id` -- una entrevista
        puede existir aunque la postulación que la generó ya no esté en
        `candidatos` (ver `remove_application`, que NUNCA borra la
        Entrevista ya creada), así que no alcanza con mirar sólo
        `candidatos`.
    Única fuente de verdad para esto -- el router la usa para decidir si
    renderiza el botón "Eliminar", y `delete_busqueda` (abajo) la vuelve a
    exigir server-side (defensa en profundidad: nunca confiar sólo en que
    el botón esté oculto)."""
    if busqueda_repository.solicitud_of(busqueda) is not None:
        return False  # una solicitud de Operaciones NUNCA es un placeholder: se cancela, no se borra
    if busqueda.get("candidatos"):
        return False
    return not any(e.get("busqueda_id") == busqueda["busqueda_id"] for e in entrevista_repository.list())


def delete_busqueda(busqueda_id: str) -> None:
    """Hard delete -- SOLO si `busqueda_is_placeholder` (owner: "un proceso
    iniciado no puede eliminarse", únicamente Cerrar en ese caso). Releva
    `BusquedaNotFoundError` tal cual (nunca oculta un 404 real detrás de la
    validación de negocio)."""
    busqueda = busqueda_repository.get(busqueda_id)
    if busqueda is None:
        raise busqueda_repository.BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
    if busqueda_repository.solicitud_of(busqueda) is not None:
        raise ValueError(
            "Una solicitud originada en Operaciones no se puede eliminar: se cancela y queda en el historial."
        )
    if not busqueda_is_placeholder(busqueda):
        raise ValueError(
            "Esta búsqueda ya tiene candidatos o entrevistas registradas -- no se puede eliminar. "
            "Usá Cerrar proceso en su lugar."
        )
    busqueda_repository.delete(busqueda_id)


# ── Solicitudes de Operaciones (REG-CH-01) ───────────────────────────────────
# El service es DUEÑO de la validación/normalización (los routers futuros NO
# validan) y de la proyección segura hacia Operaciones. La autorización
# (`operations_access.is_operations_authorized` / acceso CCHH) vive en los routers.

TIPOS_BUSQUEDA = ("interna", "proyecto")
TIPOS_CONTRATO = ("FTE", "PTE")
INGLES_NIVELES = ("No requerido", "Básico", "Intermedio", "Avanzado")
DURACION_TIPOS = ("meses", "indefinida")
DURACION_MESES_MAX = 120
# unidad explícita de la dedicación -> tope del valor (sin unidad implícita).
DEDICACION_UNIDADES = {
    "horas_por_mes": 744,
    "horas_por_semana": 168,
    "porcentaje_jornada": 100,
}
CANTIDAD_RECURSOS_MAX = 99
_MODALIDADES_PRESENCIALES = ("Híbrido", "On site")
DIAS_PRESENCIALES_HIBRIDO_MAX = 5
_TEXT_SHORT = 200
_TEXT_LONG = 4000
_MOTIVO_MAX = 1000


def _req_text(fields: dict, key: str, label: str, *, required: bool, max_len: int = _TEXT_SHORT) -> str | None:
    value = fields.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} tiene un formato inválido.")
    text = " ".join((value or "").split()) if max_len <= _TEXT_SHORT else (value or "").strip()
    if not text:
        if required:
            raise ValueError(f"Completá {label}.")
        return None
    if len(text) > max_len:
        raise ValueError(f"{label} supera el máximo de {max_len} caracteres.")
    return text


def _req_int(value, label: str, *, lo: int, hi: int) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Completá {label}.")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{label} tiene que ser un número entero.")
        value = int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not re.fullmatch(r"\d{1,6}", raw):
            raise ValueError(f"{label} tiene que ser un número entero.")
        value = int(raw)
    if not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{label} tiene que estar entre {lo} y {hi}.")
    return value


def _req_number(value, label: str, *, hi: int) -> int | float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Completá {label}.")
    if isinstance(value, str):
        raw = value.strip().replace(",", ".")
        if not re.fullmatch(r"\d{1,6}(\.\d{1,2})?", raw):
            raise ValueError(f"{label} tiene que ser un número.")
        value = float(raw)
    if not isinstance(value, (int, float)) or value <= 0 or value > hi:
        raise ValueError(f"{label} tiene que ser mayor que 0 y hasta {hi}.")
    return int(value) if float(value).is_integer() else round(float(value), 2)


def _req_seniority(value, existing: list[str], *, check_catalogs: bool = True) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("Elegí al menos un seniority.")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Seniority tiene un formato inválido.")
        v = item.strip()
        if v and v not in out:
            out.append(v)
    if not out:
        raise ValueError("Elegí al menos un seniority.")
    if check_catalogs:
        for v in out:  # catálogo gobernado, migration-safe: un valor legacy ya guardado se preserva
            _validate_seniority_governed(v, existing_value=v if v in existing else None)
    return out


def _shape_requerimiento(fields: dict, *, existing: dict | None = None, check_catalogs: bool = True) -> dict:
    """Valida y normaliza el REG-CH-01 completo. Devuelve el `requerimiento` canónico
    (lo único que se persiste como lo pedido y lo que se huellea para idempotencia).
    `existing` = requerimiento ya guardado (edición/reenvío): preserva valores
    gobernados legacy que cambiaron de catálogo. `check_catalogs=False` hace SOLO la normalización estructural (sin consultar catálogos gobernados): lo usa el reenvío de un
    `submission_id` ya aceptado, que se decide por identidad/equivalencia ANTES de cualquier validación sujeta a cambios de catálogo."""
    if not isinstance(fields, dict):
        raise ValueError("Datos de la solicitud inválidos.")
    existing = existing or {}
    nombre = _req_text(fields, "nombre_puesto", "el nombre del perfil requerido", required=True, max_len=150)
    tipo = (fields.get("tipo_busqueda") or "").strip() if isinstance(fields.get("tipo_busqueda"), str) else ""
    if tipo not in TIPOS_BUSQUEDA:
        raise ValueError("Indicá si la búsqueda es interna o para un proyecto.")
    proyecto = _req_text(fields, "proyecto", "el proyecto", required=(tipo == "proyecto"))
    if tipo != "proyecto":
        proyecto = None
    cantidad = _req_int(fields.get("cantidad_recursos"), "La cantidad de recursos", lo=1, hi=CANTIDAD_RECURSOS_MAX)
    unidad = (fields.get("dedicacion_unidad") or "").strip() if isinstance(fields.get("dedicacion_unidad"), str) else ""
    if unidad not in DEDICACION_UNIDADES:
        raise ValueError("Elegí la unidad de la dedicación (horas por mes, por semana, por día o % de jornada).")
    dedicacion = _req_number(fields.get("dedicacion_valor"), "La dedicación", hi=DEDICACION_UNIDADES[unidad])
    d_tipo = (fields.get("duracion_tipo") or "").strip() if isinstance(fields.get("duracion_tipo"), str) else ""
    if d_tipo not in DURACION_TIPOS:
        raise ValueError("Indicá la duración: en meses o indefinida.")
    d_meses = _req_int(fields.get("duracion_meses"), "La duración en meses", lo=1, hi=DURACION_MESES_MAX) \
        if d_tipo == "meses" else None
    area = _req_text(fields, "area_consultora", "el área a la que pertenecerá", required=True)
    if check_catalogs:
        _validate_area_consultora_governed(area, existing_value=existing.get("area_consultora"))
    modalidad = _req_text(fields, "modalidad", "la modalidad", required=True)
    if modalidad not in busqueda_repository.MODALIDADES:
        raise ValueError(f"Modalidad inválida. Elegí: {', '.join(busqueda_repository.MODALIDADES)}.")
    presencialidad = None
    if modalidad in _MODALIDADES_PRESENCIALES:
        # Híbrido: 1 a 5 días presenciales + lugar. On site: solo lugar (los días no se exigen ni se guardan).
        dias = _req_int(fields.get("dias_presenciales"), "La cantidad de días presenciales", lo=1, hi=DIAS_PRESENCIALES_HIBRIDO_MAX)             if modalidad == "Híbrido" else None
        presencialidad = {
            "dias": dias,
            "lugar": _req_text(fields, "lugar_trabajo", "el lugar exacto de trabajo", required=True),
        }
    seniority = _req_seniority(fields.get("seniority"), list(existing.get("seniority") or []), check_catalogs=check_catalogs)
    ingles = _req_text(fields, "ingles", "el nivel de inglés", required=True)
    if ingles not in INGLES_NIVELES:
        raise ValueError(f"Nivel de inglés inválido. Elegí: {', '.join(INGLES_NIVELES)}.")
    contrato = _req_text(fields, "tipo_contrato", "la modalidad de contratación", required=True)       # clave histórica `tipo_contrato`; es la Modalidad de contratación (Full-Time / Part-Time)
    if check_catalogs:
        _validate_modalidad_contratacion_governed(contrato, existing_value=existing.get("tipo_contrato"))   # catálogo gobernado; un valor legacy ya guardado se preserva
    inicio_raw = _req_text(fields, "fecha_inicio_prevista", "la fecha de inicio prevista", required=True)
    try:
        inicio = date.fromisoformat(inicio_raw).isoformat()
    except ValueError:
        raise ValueError("La fecha de inicio prevista no es una fecha válida (AAAA-MM-DD).") from None
    return {
        "nombre_puesto": nombre,
        "tipo_busqueda": tipo,
        "proyecto": proyecto,
        "cantidad_recursos": cantidad,
        "dedicacion": {"valor": dedicacion, "unidad": unidad},
        "duracion": {"tipo": d_tipo, "meses": d_meses},
        "area_consultora": area,
        "ubicacion": _req_text(fields, "ubicacion", "la ubicación requerida", required=True),
        "seniority": seniority,
        "modalidad": modalidad,
        "presencialidad": presencialidad,
        "responsabilidades": _req_text(fields, "responsabilidades", "las responsabilidades del puesto", required=True, max_len=_TEXT_LONG),
        "skills_excluyentes": _req_text(fields, "skills_excluyentes", "los skills técnicos excluyentes", required=True, max_len=_TEXT_LONG),
        "habilidades_blandas": _req_text(fields, "habilidades_blandas", "las habilidades blandas", required=False, max_len=_TEXT_LONG),
        "ingles": ingles,
        "certificaciones": _req_text(fields, "certificaciones", "las certificaciones", required=False, max_len=_TEXT_LONG),
        "tipo_contrato": contrato,
        "fecha_inicio_prevista": inicio,
        "comentarios": _req_text(fields, "comentarios", "los comentarios", required=False, max_len=_TEXT_LONG),
    }


def _shape_motivo(motivo, *, required: bool, label: str) -> str | None:
    if motivo is not None and not isinstance(motivo, str):
        raise ValueError(f"{label} tiene un formato inválido.")
    text = (motivo or "").strip()
    if not text:
        if required:
            raise ValueError(f"Indicá {label}.")
        return None
    if len(text) > _MOTIVO_MAX:
        raise ValueError(f"{label[0].upper()}{label[1:]} supera el máximo de {_MOTIVO_MAX} caracteres.")
    return text


def _require_actor(actor) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Falta identificar quién realiza la operación.")
    return actor.strip().lower()


_GENERIC_ACTORS = frozenset({"", "capital humano", "cchh", "operaciones"})       # rótulos de ÁREA (dato legado), nunca una identidad de usuario


def register_candidate_contact(busqueda_id: str, application_id: str, accion: str, situacion: str, canal: str, *, actor: str | None, event_id: str | None = None) -> tuple[dict, bool]:
    """Trazabilidad de la comunicación con un candidato desde el pipeline (ver `busqueda_repository.log_candidate_contact`). Valida la situación/canal contra el diálogo de contacto, que el candidato exista y que el canal
    sea utilizable cuando se registra una APERTURA (no se registra abrir WhatsApp/correo si el dato de contacto no existe). `ValueError` = pedido inválido; `LookupError` = no encontrado."""
    from webapp.services import human_capital_contact as contact   # import diferido: el módulo de plantillas no depende del servicio
    if accion not in busqueda_repository.CONTACTO_ACCIONES:
        raise ValueError("Acción de contacto inválida.")
    if situacion not in dict(contact.TIPOS_MENSAJE) or canal not in dict(contact.CANALES_MENSAJE):
        raise ValueError("Situación o canal inválidos.")
    event_id = (event_id or "").strip()[:64] or None
    busqueda = busqueda_repository.get(busqueda_id)
    if busqueda is None:
        raise LookupError("Búsqueda no encontrada.")
    app = next((a for a in busqueda.get("candidatos", []) if a.get("application_id") == application_id), None)
    if app is None:
        raise LookupError("Postulación no encontrada.")
    if busqueda.get("estado") != "activo":
        raise ValueError("El proceso no está activo.")
    profile = profile_repository.get(app.get("profile_id")) if app.get("profile_id") else None
    if profile is None or profile.get("deleted_at"):
        raise LookupError("El candidato ya no está disponible.")
    if accion != "mensaje_copiado" and not (contact.whatsapp_digits(profile.get("phone")) if canal == "whatsapp" else contact.valid_email(profile.get("email"))):
        raise ValueError("El candidato no tiene cargado el dato de contacto de ese canal.")
    return busqueda_repository.log_candidate_contact(
        busqueda_id, application_id, accion=accion, situacion=situacion, canal=canal, actor=actor, event_id=event_id, names=_candidate_display_name)


def _contact_row_texts(h: dict) -> dict:
    """Textos visibles del evento de contacto: «Movimiento» = situación elegida + canal («Primer contacto por WhatsApp», «Actualización del proceso por mail»); «Detalle» distingue copiar de abrir sin afirmar el envío."""
    from webapp.services import human_capital_contact as contact
    base = str(dict(contact.TIPOS_MENSAJE).get(h.get("situacion")) or "Contacto").split(" (")[0]
    canal = h.get("canal")
    canal_mov = {"whatsapp": "WhatsApp", "mail": "mail"}.get(canal, canal or "—")
    canal_det = {"whatsapp": "WhatsApp", "mail": "correo"}.get(canal, canal or "—")
    accion = h.get("accion")
    detalle = ("Mensaje copiado" if accion == "mensaje_copiado"
               else f"Apertura de {canal_det}" if accion == "canal_abierto"
               else f"Apertura solicitada ({canal_det})")
    return {"movimiento": f"{base} por {canal_mov}", "detalle_contacto": detalle}


def _historial_rows(entries, *, include_cchh_actors: bool) -> list[dict]:
    """Filas (lista blanca) del historial del proceso. Los movimientos de CANDIDATOS (`candidato_*`) son información interna de Capital Humano: SOLO se proyectan en las pantallas de CCHH
    (`include_cchh_actors=True`), nunca hacia Operaciones. «Realizado por» = la identidad registrada del ejecutor; un legado que solo guarda el rótulo de área no se promueve a identidad."""
    rows = []
    for h in entries or []:
        is_candidate = str(h.get("transicion") or "").startswith("candidato_")
        if is_candidate and not include_cchh_actors:
            continue
        row = {k: h.get(k) for k in ("transicion", "desde", "hasta", "fecha", "actor_tipo", "motivo")}
        if h.get("actor_tipo") == busqueda_repository.ORIGEN_OPERACIONES:
            row["actor"] = h.get("actor")
        elif include_cchh_actors and str(h.get("actor") or "").strip().lower() not in _GENERIC_ACTORS:
            row["actor"] = h.get("actor")
        if is_candidate:
            row.update({"candidato": h.get("candidato"), "profile_id": h.get("profile_id"), "resultado": h.get("resultado")})
            if h.get("transicion") == busqueda_repository.CANDIDATO_CONTACTO:
                row.update({"accion": h.get("accion"), "situacion": h.get("situacion"), "canal": h.get("canal"), **_contact_row_texts(h)})
        rows.append(row)
    return rows


def proceso_historial_view(doc: dict) -> dict:
    """Historial del proceso para las pantallas de CCHH: el de la solicitud si la hay, o el de nivel documento (alta directa de CCHH). Mismo esquema de filas que `solicitud_view`."""
    return {"historial": _historial_rows(busqueda_repository.historial_proceso_of(doc), include_cchh_actors=True), "ejecutores_cchh_visibles": True}


def solicitud_view(doc: dict, *, include_cchh_actors: bool = False) -> dict | None:
    """Proyección SEGURA de una solicitud para Operaciones (lista blanca).
    NUNCA incluye candidatos/postulaciones, ni datos del Banco de CVs, ni el
    JD/palabras clave/descripción internos de CCHH, ni el `origen` completo.
    Los emails sólo se muestran para actores de Operaciones. `None` si la
    búsqueda no es una solicitud (históricas y altas directas de CCHH)."""
    sol = busqueda_repository.solicitud_of(doc)
    if sol is None:
        return None
    estado = sol.get("estado")
    obs = sol.get("observacion") if isinstance(sol.get("observacion"), dict) else None
    historial = _historial_rows(sol.get("historial"), include_cchh_actors=include_cchh_actors)
    view = {
        "busqueda_id": doc.get("busqueda_id"),
        "solicitud_id": sol.get("submission_id") or doc.get("busqueda_id"),
        "revision": doc.get("revision", 1),
        "estado_solicitud": estado,
        "estado": doc.get("estado"),
        "fecha_apertura": doc.get("fecha_apertura"),
        "fecha_cierre": doc.get("fecha_cierre"),
        "nombre_puesto": doc.get("nombre_puesto"),
        "area_consultora": doc.get("area_consultora"),
        "modalidad": doc.get("modalidad"),
        "solicitante": sol.get("solicitante"),
        "enviada_en": sol.get("enviada_en"),
        "ultimo_envio_en": sol.get("ultimo_envio_en"),
        "observacion": {"motivo": obs.get("motivo"), "en": obs.get("en")} if obs else None,
        "tomada_en": sol.get("tomada_en"),
        "cancelada_en": sol.get("cancelada_en"),
        "motivo_cancelacion": sol.get("motivo_cancelacion"),
        "requerimiento": copy.deepcopy(doc.get("requerimiento") or {}),
        "historial": historial,
        # Indicadores DERIVADOS (no son estados ni se persisten): para la futura bandeja de CCHH (I3) y los listados.
        "categoria": busqueda_repository.solicitud_categoria(doc),
        "reenviada": busqueda_repository.solicitud_reenviada(doc),
        "reenvios": busqueda_repository.solicitud_reenvios(doc),
        # Qué puede hacer Operaciones HOY (el router lo usa para habilitar acciones; la regla real vive en el repositorio).
        "puede_editar": estado == busqueda_repository.SOLICITUD_ENVIADA and not busqueda_repository.solicitud_fue_tomada(doc),
        "puede_corregir": estado == busqueda_repository.SOLICITUD_OBSERVADA and not busqueda_repository.solicitud_fue_tomada(doc),
        "puede_cancelar": estado in (busqueda_repository.SOLICITUD_ENVIADA, busqueda_repository.SOLICITUD_OBSERVADA) and not busqueda_repository.solicitud_fue_tomada(doc),
        "puede_eliminar": busqueda_repository.solicitud_puede_eliminar(doc),
        "solo_lectura": estado in (busqueda_repository.SOLICITUD_EN_GESTION, busqueda_repository.SOLICITUD_CANCELADA),
    }
    if include_cchh_actors:                                       # solo la vista de Capital Humano agrega esta clave (la proyección segura para Operaciones conserva su lista blanca exacta)
        view["ejecutores_cchh_visibles"] = True
    return view


def list_busquedas_gestionadas() -> list[dict]:
    """Búsquedas que CCHH efectivamente gestiona: históricas, altas directas y solicitudes ya tomadas (excluye pendientes, observadas y canceladas). `list_busquedas()` NO cambia
    (sigue devolviendo todo); I3 decidirá qué superficie usa cada una."""
    return [b for b in busqueda_repository.list() if busqueda_repository.es_gestionada(b)]


def resumen_solicitudes() -> dict:
    """Conteo de solicitudes de Operaciones por categoría (`pendiente`/`observada`/`gestionada`/`cancelada`) + cuántas pendientes son reenvíos. Derivado; sin escrituras."""
    out = {"pendiente": 0, "observada": 0, "gestionada": 0, "cancelada": 0, "reenviadas": 0, "total": 0}
    for doc in busqueda_repository.list():
        cat = busqueda_repository.solicitud_categoria(doc)
        if cat is None:
            continue
        out[cat] += 1
        out["total"] += 1
        out["reenviadas"] += int(busqueda_repository.solicitud_reenviada(doc))
    return out


def get_solicitud_view(busqueda_id: str) -> dict | None:
    doc = busqueda_repository.get(busqueda_id)
    return solicitud_view(doc) if doc is not None else None


def list_solicitudes_view() -> list[dict]:
    """Todas las solicitudes originadas en Operaciones (cualquier usuario autorizado de
    Operaciones las ve y gestiona), más recientes primero. Las búsquedas sin solicitud
    (históricas / alta directa de CCHH) NO aparecen."""
    rows = [v for v in (solicitud_view(d) for d in busqueda_repository.list()) if v is not None]
    return sorted(rows, key=lambda r: (r.get("enviada_en") or "", r["busqueda_id"]), reverse=True)


def _existing_requerimiento(busqueda_id: str) -> dict:
    doc = busqueda_repository.get(busqueda_id)
    if doc is None:
        raise busqueda_repository.BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
    return doc.get("requerimiento") if isinstance(doc.get("requerimiento"), dict) else {}


def create_solicitud(submission_id: str, fields: dict, actor: str) -> dict:
    """Operaciones crea y envía una solicitud. Idempotente por `submission_id`
    (UUID): el mismo envío repetido devuelve la misma solicitud; el mismo id con
    otro contenido → `BusquedaSubmissionConflictError`. Devuelve la proyección segura."""
    sid = busqueda_repository.normalize_submission_id(submission_id)
    actor = _require_actor(actor)
    existing = busqueda_repository.find_submission(sid)
    if existing is not None:
        # Envío ya aceptado: se decide por IDENTIDAD y EQUIVALENCIA del contenido original (huella), con normalización solo estructural, ANTES de cualquier validación
        # sujeta a catálogos que pudieron cambiar. Un contenido distinto con el mismo identificador NUNCA se acepta en silencio.
        sol = busqueda_repository.solicitud_of(existing)
        same_identity = (sol is not None and busqueda_repository.origen_tipo(existing) == busqueda_repository.ORIGEN_OPERACIONES
                         and sol.get("submission_id") == sid and existing.get("busqueda_id") == sid)
        try:
            fingerprint = busqueda_repository.content_fingerprint(_shape_requerimiento(fields, check_catalogs=False), actor)
        except ValueError:
            fingerprint = None
        if same_identity and fingerprint is not None and sol.get("submission_fingerprint") == fingerprint:
            return solicitud_view(existing)
        raise busqueda_repository.BusquedaSubmissionConflictError("Ese identificador de envío ya se usó con un contenido distinto.")
    doc = busqueda_repository.create_solicitud(sid, _shape_requerimiento(fields), actor)
    return solicitud_view(doc)


def edit_solicitud(busqueda_id: str, expected_revision: int, fields: dict, actor: str) -> dict:
    """Operaciones edita una solicitud `enviada` (aún no tomada). Proyección segura."""
    actor = _require_actor(actor)
    req = _shape_requerimiento(fields, existing=_existing_requerimiento(busqueda_id))
    return solicitud_view(busqueda_repository.edit_solicitud(busqueda_id, expected_revision, req, actor))


def resubmit_solicitud(busqueda_id: str, expected_revision: int, fields: dict, actor: str) -> dict:
    """Operaciones corrige una solicitud `observada` y la reenvía. Proyección segura."""
    actor = _require_actor(actor)
    req = _shape_requerimiento(fields, existing=_existing_requerimiento(busqueda_id))
    return solicitud_view(busqueda_repository.resubmit_solicitud(busqueda_id, expected_revision, req, actor))


def cancel_solicitud(busqueda_id: str, expected_revision: int, actor: str, motivo: str | None = None) -> dict:
    """Operaciones cancela antes de que CCHH la tome (motivo opcional). Proyección segura."""
    actor = _require_actor(actor)
    motivo = _shape_motivo(motivo, required=False, label="el motivo de la cancelación")
    return solicitud_view(busqueda_repository.cancel_solicitud(busqueda_id, expected_revision, motivo, actor))


def delete_solicitud(busqueda_id: str, expected_revision: int, actor: str) -> dict:
    """Operaciones elimina una solicitud que CCHH NO tomó nunca (≠ cancelar: la cancelación conserva el registro; esto lo borra). Además de las guardas del repositorio (historial de toma,
    estado, origen, candidatos, revisión bajo lock) exige que no cuelgue nada de ella: entrevistas, onboarding ni publicaciones vinculadas."""
    actor = _require_actor(actor)
    doc = busqueda_repository.get(busqueda_id)
    if doc is None:
        raise busqueda_repository.BusquedaNotFoundError(f"La búsqueda {busqueda_id} no existe.")
    if busqueda_repository.solicitud_of(doc) is not None and not busqueda_repository.solicitud_fue_tomada(doc):
        linked = (entrevista_repository.list() + onboarding_repository.list() + publicacion_repository.list())
        if any(x.get("busqueda_id") == busqueda_id for x in linked):
            raise busqueda_repository.BusquedaInvalidTransitionError("La solicitud tiene entrevistas o procesos asociados: no se puede eliminar.")
    return busqueda_repository.delete_solicitud(busqueda_id, expected_revision, actor)


def observe_solicitud(busqueda_id: str, expected_revision: int, motivo: str, actor: str) -> dict:
    """CCHH observa una solicitud `enviada` (motivo OBLIGATORIO). Devuelve la búsqueda completa."""
    actor = _require_actor(actor)
    motivo = _shape_motivo(motivo, required=True, label="el motivo de la observación")
    return busqueda_repository.observe_solicitud(busqueda_id, expected_revision, motivo, actor)


def take_solicitud(busqueda_id: str, expected_revision: int, actor: str) -> dict:
    """CCHH toma explícitamente la solicitud en gestión. Devuelve la búsqueda completa."""
    return busqueda_repository.take_solicitud(busqueda_id, expected_revision, _require_actor(actor))


def add_candidate_to_busqueda(busqueda_id: str, profile_id: str, *, actor: str | None = None) -> dict:
    profile = profile_repository.get(profile_id)
    if profile is None or profile.get("deleted_at"):
        raise ValueError("El candidato no existe o fue eliminado del Banco de CVs.")
    return busqueda_repository.add_candidate(busqueda_id, profile_id, actor=actor, names=_candidate_display_name)


def remove_application(busqueda_id: str, application_id: str, *, actor: str | None = None) -> dict:
    return busqueda_repository.remove_candidate(busqueda_id, application_id, actor=actor, names=_candidate_display_name)


# Etapas que implican que "hubo entrevista" -- MISMO gate que
# actualizarEtapaPipeline en la referencia (lib/actions/candidatos.ts):
# "cualquier etapa posterior a la preselección implica que hubo entrevista
# CCHH: si todavía no está registrada (por ejemplo, se pasó directo a
# Entrevista Líder), se crea igual". Verbatim, sin inventar.
_ETAPAS_QUE_DISPARAN_ENTREVISTA = ("Entrevista CCHH", "Entrevista Líder", "Decisión Final")


def move_application_stage(busqueda_id: str, application_id: str, etapa: str,
                           resultado_final: str | None, *, actor: str | None = None) -> dict:
    if etapa not in busqueda_repository.ETAPAS:
        raise ValueError("Etapa inválida.")
    resultado = (resultado_final or "").strip() or None
    if etapa == busqueda_repository.ETAPA_FINAL:
        if resultado is not None and resultado not in busqueda_repository.RESULTADOS_FINALES:
            raise ValueError("Resultado final inválido.")
    else:
        resultado = None  # el repo también lo fuerza; se valida acá el contrato
    busqueda = busqueda_repository.move_stage(busqueda_id, application_id, etapa, resultado, actor=actor, names=_candidate_display_name)
    sync_onboardings_for_busqueda(busqueda)             # Finalizado + Ingreso => Onboarding (en una sola operación o con el resultado asignado después)
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


def busqueda_attention(busqueda: dict, onboarding_pendiente: int = 0) -> dict:
    """Señales derivadas de atención de un proceso, para badges + filtros. Sólo
    presentación/UX — no muta el documento ni cambia su lifecycle. `estancada`
    aplica ÚNICAMENTE a procesos activos (igual que la referencia)."""
    dias = busqueda_dias_sin_movimiento(busqueda)
    activo = busqueda.get("estado") == "activo"
    return {
        "dias_sin_movimiento": dias,
        "estancada": bool(activo and dias is not None and dias >= BUSQUEDA_UMBRAL_ESTANCADA_DIAS),
        "sin_requerimiento": not busqueda.get("pedido_formal_recibido"),
        # Cantidad de Onboardings de este proceso con `completado == False` (la lista la arma `onboarding_pending_by_busqueda`). Señal derivada: no duplica el proceso.
        "onboarding_pendiente": onboarding_pendiente,
    }


def busqueda_with_attention(busqueda: dict, onboarding_pendiente: int = 0) -> dict:
    """Copia del proceso con `attention` embebido (nunca se persiste)."""
    return {**busqueda, "attention": busqueda_attention(busqueda, onboarding_pendiente)}


# ── ENTREVISTAS ───────────────────────────────────────────────────────────────
# Dominio verbatim de la referencia (ver entrevista_repository.py, module
# docstring, para las citas exactas de schema.sql/lib/entrevistaAutomatica.ts).

# «Remuneración pretendida» es un dato SENSIBLE (ver `human_capital_access.can_view_sensitive_data`). Todo lo que sale hacia una vista pasa por `mask_entrevista_compensation`;
# toda escritura exige `allow_compensation=True` (default False = fail-closed: el valor persistido se conserva y lo enviado se descarta).
COMPENSATION_MASK = "****"


def mask_entrevista_compensation(entrevista: dict, allow: bool) -> dict:
    """Copia de la entrevista con el importe enmascarado (`****`, SIEMPRE, haya o no valor: no se revela ni su presencia) si `allow` es False. No toca el dato persistido."""
    if allow:
        return entrevista
    return {**entrevista, "remuneracion_pretendida": COMPENSATION_MASK}


def list_entrevistas() -> list[dict]:
    return entrevista_repository.list()


def get_entrevista(entrevista_id: str) -> dict | None:
    return entrevista_repository.get(entrevista_id)


def _norm_catalog_key(value) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").casefold())


def busqueda_modalidad_contratacion(busqueda: dict | None) -> str | None:
    """Modalidad de contratación (Full-Time / Part-Time) definida en el requerimiento ORIGINAL de Operaciones (`requerimiento.tipo_contrato`: clave histórica de ese campo, validada contra el MISMO catálogo
    gobernado `modalidad_contratacion` que usa la Entrevista), devuelta con la grafía del catálogo vigente. Solo se acepta un valor COMPATIBLE (presente en el catálogo, sin distinguir mayúsculas/guiones/espacios);
    cualquier otro (legacy, vacío, catálogo no disponible) => None. NO se deriva de `dedicacion` (carga horaria: 40 h/semana, etc.) ni de `modalidad` (Híbrido/Remoto/On site): son atributos distintos."""
    raw = ((busqueda or {}).get("requerimiento") or {}).get("tipo_contrato")
    wanted = _norm_catalog_key(raw)
    if not wanted:
        return None
    return next((v for v in catalog_repo.catalog_values("modalidad_contratacion") if _norm_catalog_key(v) == wanted), None)


def resolve_entrevista_modalidad(stored, busqueda: dict | None, *, edited: bool = False) -> tuple[str | None, str | None]:
    """Precedencia de la Modalidad de contratación de una entrevista -> `(valor, origen)`:
      1. el valor GUARDADO en la entrevista (jamás se pisa);
      2. el valor específico del candidato — hoy NO aplica: el Banco de CVs no tiene ese atributo (no se inventa);
      3. el valor compatible del requerimiento del proceso (`busqueda_modalidad_contratacion`);
      4. sin especificar.
    `edited=True` (la entrevista ya se editó alguna vez) tratan el vacío como decisión del usuario: no se precarga nada. Es una resolución de LECTURA: no persiste ni modifica retroactivamente."""
    if str(stored or "").strip():
        return str(stored).strip(), "entrevista"
    if edited:
        return None, None
    value = busqueda_modalidad_contratacion(busqueda)
    return (value, "busqueda") if value else (None, None)


def _shape_entrevista_fields(fields: dict, *, existing: dict | None = None, allow_compensation: bool = False) -> dict:
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
        # Sin permiso: el importe enviado se DESCARTA y se conserva el persistido (None en un alta); jamás se sobrescribe ni se borra indirectamente.
        "remuneracion_pretendida": ((fields.get("remuneracion_pretendida") or "").strip() or None) if allow_compensation else (existing or {}).get("remuneracion_pretendida"),
        "observaciones": (fields.get("observaciones") or "").strip() or None,
        "resumen_entrevista_tecnica": (fields.get("resumen_entrevista_tecnica") or "").strip() or None,
    }


def create_entrevista_manual(profile_id: str, fields: dict, *, allow_compensation: bool = False) -> dict:
    """Alta manual (candidato del Banco todavía sin ninguna Búsqueda) -- MISMO
    caso que `crearEntrevistaManual` en la referencia (banco_cv_id, sin
    proceso_id/candidato_id)."""
    profile = profile_repository.get(profile_id)
    if profile is None or profile.get("deleted_at"):
        raise ValueError("Elegí un candidato del Banco de CVs.")
    return entrevista_repository.create(
        _shape_entrevista_fields(fields, allow_compensation=allow_compensation), profile_id=profile_id, busqueda_id=None, application_id=None)


def update_entrevista(entrevista_id: str, expected_revision: int, fields: dict, *, allow_compensation: bool = False) -> dict:
    existing = entrevista_repository.get(entrevista_id)
    updated = entrevista_repository.update(
        entrevista_id, expected_revision, _shape_entrevista_fields(fields, existing=existing, allow_compensation=allow_compensation))
    sync_onboarding_area_from_entrevista(updated)
    return updated


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
        "modalidad_contratacion": resolve_entrevista_modalidad(None, busqueda)[0],   # condiciones del requerimiento (si son compatibles)
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


def entrevista_view(entrevista: dict, allow_compensation: bool = False) -> dict:
    """Enriquece una entrevista con Profile (maestro de datos personales +
    `source`, del que se deriva SIEMPRE el origen mostrado -- MISMA prioridad
    que `origenDeEntrevista` de la referencia, pero sin duplicar el campo) y
    Búsqueda (de la que se deriva el puesto evaluado mientras haya
    `busqueda_id`, MISMA prioridad que `puestoDeEntrevista`)."""
    profile = profile_repository.get(entrevista.get("profile_id"))
    busqueda = busqueda_repository.get(entrevista["busqueda_id"]) if entrevista.get("busqueda_id") else None
    puesto = (busqueda or {}).get("nombre_puesto") or entrevista.get("puesto_evaluado")
    return {
        **mask_entrevista_compensation(entrevista, allow_compensation),
        "profile": profile,
        "busqueda": busqueda,
        "origen": (profile or {}).get("source"),
        "puesto_mostrado": puesto,
        "puesto_es_derivado": bool(busqueda),
    }


def list_entrevistas_view(allow_compensation: bool = False) -> list[dict]:
    return [entrevista_view(e, allow_compensation) for e in list_entrevistas()]


# ── ONBOARDING ───────────────────────────────────────────────────────────────
# Dominio verbatim de la referencia (ver onboarding_repository.py, module docstring).
def is_ingreso_application(app: dict) -> bool:
    """Condición ÚNICA de nacimiento de un Onboarding: la candidatura quedó Finalizado Y con resultado Ingreso. Se evalúa sobre el ESTADO resultante (no sobre el
    evento), así vale tanto si se mueve a Finalizado + Ingreso en una sola operación como si primero quedó Finalizado y el resultado se asignó después."""
    return app.get("etapa") == busqueda_repository.ETAPA_FINAL and app.get("resultado_final") == "Ingreso"


def ensure_onboarding_for_application(busqueda: dict, app: dict) -> dict | None:
    """Idempotente: crea el Onboarding de la candidatura si cumple `is_ingreso_application` y todavía no existe. El área se trae de la Entrevista de esa
    candidatura (si tiene), como `crearOnboardingSiNoExiste` en la referencia; `fecha_ingreso` = hoy. Best-effort: un fallo de persistencia NUNCA rompe la
    mutación de pipeline ya confirmada (mismo criterio que la entrevista automática); `reconcile_onboardings` lo repone."""
    if not is_ingreso_application(app) or not app.get("application_id"):
        return None
    entrevista = entrevista_repository.find_by_profile_and_busqueda(app["profile_id"], busqueda["busqueda_id"])
    try:
        doc, _created = onboarding_repository.create_if_absent(
            application_id=app["application_id"], profile_id=app["profile_id"], busqueda_id=busqueda["busqueda_id"],
            area=(entrevista or {}).get("area"))
        return doc
    except (onboarding_repository.OnboardingStorageError, onboarding_repository.OnboardingRemoteOutcomeUnknownError):
        _logger.warning("human_capital.onboarding_automatico_fallo application_id=%s", app.get("application_id"))
        return None


def sync_onboardings_for_busqueda(busqueda: dict) -> None:
    """Punto central tras una mutación de las candidaturas de una Búsqueda: asegura el Onboarding de cada una que esté Finalizado + Ingreso."""
    for app in busqueda.get("candidatos", []):
        ensure_onboarding_for_application(busqueda, app)


def reconcile_onboardings() -> int:
    """BACKFILL explícito (NO se invoca desde ningún request): repone los Onboardings de candidaturas que YA están Finalizado + Ingreso y no lo tienen (p. ej. ingresos
    cargados antes de existir esta sección). Idempotente. Devuelve cuántos creó. Paso operativo DIFERIDO (`Historical onboarding backfill = deferred operational step`): se
    ejecutará desde un operador/migración explícita; navegar `GET /cchh/onboarding` jamás escribe."""
    created = 0
    for busqueda in busqueda_repository.list():
        for app in busqueda.get("candidatos", []):
            if is_ingreso_application(app) and onboarding_repository.get(app["application_id"]) is None:
                created += 1 if ensure_onboarding_for_application(busqueda, app) is not None else 0
    return created


def sync_onboarding_area_from_entrevista(entrevista: dict) -> None:
    """Editar el área de la Entrevista de una candidatura que ya tiene Onboarding mantiene sincronizada el área del Onboarding (`sincronizarAreaOnboardingDesdeEntrevista`
    en la referencia). Sin Onboarding, no hace nada. Best-effort."""
    application_id = entrevista.get("application_id")
    if not application_id:
        return
    try:
        onboarding_repository.set_area(application_id, entrevista.get("area"))
    except (onboarding_repository.OnboardingStorageError, onboarding_repository.OnboardingRemoteOutcomeUnknownError):
        _logger.warning("human_capital.onboarding_area_sync_fallo application_id=%s", application_id)


def list_onboardings() -> list[dict]:
    return onboarding_repository.list()


def get_onboarding(application_id: str) -> dict | None:
    return onboarding_repository.get(application_id)


def onboarding_view(onboarding: dict) -> dict:
    """Enriquece un Onboarding con el Profile (maestro de datos personales, leído en vivo), la Búsqueda (puesto/proceso) y el progreso derivado del checklist."""
    profile = profile_repository.get(onboarding.get("profile_id"))
    busqueda = busqueda_repository.get(onboarding["busqueda_id"]) if onboarding.get("busqueda_id") else None
    return {
        **onboarding,
        "profile": profile,
        "busqueda": busqueda,
        "puesto_mostrado": (busqueda or {}).get("nombre_puesto"),
        "progress": onboarding_checklist.progress(onboarding.get("checklist")),
        "checklist_items": onboarding_checklist.items_view(onboarding.get("checklist")),
    }


def list_onboardings_view() -> list[dict]:
    return [onboarding_view(o) for o in list_onboardings()]


SIN_AREA = "__sin_area__"                  # valor de un filtro Área para las filas SIN área (no es una taxonomía: es la ausencia de valor)
ONBOARDING_SIN_AREA = SIN_AREA
ENTREVISTA_SIN_ORIGEN = "__sin_origen__"   # ídem para Origen / Puesto en el listado de Entrevistas
ENTREVISTA_SIN_PUESTO = "__sin_puesto__"


def _onboarding_search_haystack(row: dict) -> str:
    """Texto sobre el que busca el campo Buscar del listado: lo VISIBLE de la fila (candidato, puesto, área, fecha de ingreso en su formato de presentación DD-MM-YYYY
    y en ISO, y el texto de progreso tal cual se muestra) + la oferta cerrada (dato relevante de la fila). MISMA normalización que la búsqueda de Candidatos."""
    profile = row.get("profile") or {}
    fecha = row.get("fecha_ingreso") or ""
    parts = fecha.split("-")
    fecha_dmy = "-".join(reversed(parts)) if len(parts) == 3 else fecha
    prog = row.get("progress") or {}
    progreso = "Completado" if row.get("completado") else f"{prog.get('hechos', 0)}/{prog.get('total', 0)} · {prog.get('porcentaje', 0)}%"
    values = [f"{profile.get('first_name') or ''} {profile.get('last_name') or ''}", row.get("puesto_mostrado"), row.get("area"), fecha, fecha_dmy, progreso,
              row.get("oferta_cerrada")]
    return " ".join(_normalize_text(v) for v in values if v)


def filter_onboardings(rows: list[dict], *, estado: str = "todos", area: str = "", q: str = "") -> list[dict]:
    """AND entre Estado (pendientes/completos por `completado`), Área (igualdad EXACTA sobre el área EFECTIVA de la fila, incluido un valor legacy fuera de catálogo;
    `ONBOARDING_SIN_AREA` = filas sin área) y Buscar (substring normalizado, case-insensitive, sobre `_onboarding_search_haystack`). Un filtro vacío no restringe."""
    wanted_q = _normalize_text(q)
    out = []
    for row in rows:
        if estado == "pendientes" and row.get("completado"):
            continue
        if estado == "completos" and not row.get("completado"):
            continue
        if area:
            effective = (row.get("area") or "").strip()
            if area == ONBOARDING_SIN_AREA:
                if effective:
                    continue
            elif effective != area:
                continue
        if wanted_q and wanted_q not in _onboarding_search_haystack(row):
            continue
        out.append(row)
    return out


def _entrevista_search_haystack(row: dict, include_compensation: bool = True) -> str:
    """Texto sobre el que busca el campo Buscar del listado de Entrevistas: el contenido VISIBLE de la fila (candidato, origen, puesto, área, fecha de entrevista en DD-MM-YYYY y ISO,
    modalidad, remuneración y la marca «No concretada»). Nunca ids técnicos. MISMA normalización que Candidatos/Onboarding."""
    profile = row.get("profile") or {}
    fecha = row.get("fecha_entrevista") or ""
    parts = fecha.split("-")
    fecha_dmy = "-".join(reversed(parts)) if len(parts) == 3 else fecha
    values = [f"{profile.get('first_name') or ''} {profile.get('last_name') or ''}", row.get("origen"), row.get("puesto_mostrado"), row.get("area"), fecha, fecha_dmy,
              row.get("modalidad_contratacion"), row.get("remuneracion_pretendida") if include_compensation else None, None if row.get("concretada", True) else "No concretada"]
    return " ".join(_normalize_text(v) for v in values if v)


def filter_entrevistas(rows: list[dict], *, origen: str = "", puesto: str = "", area: str = "", q: str = "", include_compensation: bool = True) -> list[dict]:
    """AND entre Origen (igualdad EXACTA sobre el origen EFECTIVO de la fila = Profile.source; `ENTREVISTA_SIN_ORIGEN` = sin origen), Puesto (igualdad exacta sobre el puesto mostrado;
    `ENTREVISTA_SIN_PUESTO` = sin puesto), Área (igualdad exacta, incluido un legacy fuera de catálogo; `SIN_AREA` = sin área) y Buscar (substring normalizado sobre
    `_entrevista_search_haystack`). Un filtro vacío no restringe."""
    def matches(value, wanted, sentinel):
        effective = (value or "").strip()
        if not wanted:
            return True
        return not effective if wanted == sentinel else effective == wanted
    wanted_q = _normalize_text(q)
    out = []
    for row in rows:
        if (matches(row.get("origen"), origen, ENTREVISTA_SIN_ORIGEN) and matches(row.get("puesto_mostrado"), puesto, ENTREVISTA_SIN_PUESTO)
                and matches(row.get("area"), area, SIN_AREA) and (not wanted_q or wanted_q in _entrevista_search_haystack(row, include_compensation))):
            out.append(row)
    return out


def _shape_onboarding_fields(fields: dict, *, existing: dict | None = None) -> dict:
    """`fecha_ingreso` es obligatoria (NOT NULL en la referencia); `oferta_cerrada` es texto libre opcional; `area` está GOBERNADA por `area_consultora` (MISMA
    taxonomía que Entrevista.area/Búsqueda.area_consultora: es la que se hereda de la entrevista y se sincroniza), con el contrato migration-safe habitual."""
    fecha = (fields.get("fecha_ingreso") or "").strip()
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise ValueError("La fecha de ingreso es obligatoria y tiene que ser una fecha válida.") from None
    area = (fields.get("area") or "").strip() or None
    _validate_area_consultora_governed(area, existing_value=(existing or {}).get("area"))
    return {"fecha_ingreso": fecha, "oferta_cerrada": (fields.get("oferta_cerrada") or "").strip() or None, "area": area}


def update_onboarding(application_id: str, expected_revision: int, fields: dict) -> dict:
    existing = onboarding_repository.get(application_id)
    return onboarding_repository.update(
        application_id, expected_revision, _shape_onboarding_fields(fields, existing=existing))


def set_onboarding_item(application_id: str, key: str, checked: bool) -> dict:
    return onboarding_repository.set_item(application_id, key, checked)


def onboarding_pending_by_busqueda() -> dict[str, int]:
    """`{busqueda_id: cantidad de Onboardings con completado == False}`: la señal «Onboarding pendiente» de Búsquedas. Un fallo de lectura NUNCA rompe el listado
    de Búsquedas (la señal simplemente no se muestra)."""
    try:
        docs = onboarding_repository.list()
    except onboarding_repository.OnboardingStorageError:
        _logger.warning("human_capital.onboarding_pendientes_lectura_fallo")
        return {}
    out: dict[str, int] = {}
    for o in docs:
        if not o.get("completado") and o.get("busqueda_id"):
            out[o["busqueda_id"]] = out.get(o["busqueda_id"], 0) + 1
    return out


# ── PUBLICACIONES / AVISOS ───────────────────────────────────────────────────
# Dominio verbatim de la referencia (ver publicacion_repository.py, module docstring): registro histórico plano, sin estados; `posicion` es un SNAPSHOT.
_PERIODO_RE = re.compile(r"^\d{4}-Q[1-4]$")


def periodo_desde_fecha(d: date) -> str:
    """Trimestre «YYYY-Qn» de una fecha (MISMA regla que `periodoDesdeFecha` de la referencia)."""
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def periodo_actual(today: date | None = None) -> str:
    return periodo_desde_fecha(today or date.today())


def periodos_disponibles(today: date | None = None) -> list[str]:
    """Opciones de período de un ALTA (MISMA ventana que `periodosDisponibles` de la referencia): año actual y el siguiente, trimestres 4→1, más recientes primero."""
    year = (today or date.today()).year
    return [f"{y}-Q{q}" for y in (year + 1, year) for q in (4, 3, 2, 1)]


def _validate_periodo(value: str | None, *, existing_value: str | None, today: date | None = None) -> str:
    """`YYYY-Qn` obligatorio. Un ALTA acepta sólo la ventana calculada; una EDICIÓN conserva sin cambios un período histórico (fuera de la ventana) ya guardado y exige la ventana
    sólo si se CAMBIA (history-safe: no se vuelve imposible editar un registro viejo porque cambió el año)."""
    v = (value or "").strip()
    if not v:
        raise ValueError("Elegí un período.")
    if not _PERIODO_RE.match(v):
        raise ValueError("El período tiene que tener el formato AAAA-Tn (por ejemplo 2026-Q3).")
    if v == existing_value:
        return v
    if v not in periodos_disponibles(today):
        raise ValueError("Elegí un período de la lista.")
    return v


def _validate_canal_publicacion_governed(new_value: str | None, *, existing_value: str | None) -> None:
    """Canal = taxonomía gobernada `canal_publicacion` (MISMO contrato migration-safe que Seniority/Fuente/Área): un alta exige un valor ACTIVO; una edición conserva un valor histórico
    fuera de catálogo si no se toca. Sin catálogo disponible el único camino es fallar (cerrado), sin fallback hardcodeado."""
    if _is_governed_value_allowed("canal_publicacion", new_value, existing_value=existing_value):
        return
    active = catalog_repo.catalog_values("canal_publicacion")
    if active:
        raise ValueError(f"Canal inválido. Elegí uno del catálogo vigente: {', '.join(active)}.")
    raise ValueError("El catálogo de Canales de publicación todavía no está disponible. Refrescá el catálogo antes de cargar una publicación.")


def _shape_publicacion_fields(fields: dict, *, existing: dict | None = None, today: date | None = None) -> dict:
    """Valida y arma los campos persistibles. SNAPSHOT de `posicion`: con una Búsqueda NUEVA (alta, o edición que cambia el vínculo) se COPIA su `nombre_puesto`; con el MISMO vínculo se
    conserva la `posicion` ya guardada (nunca se re-deriva ni se sincroniza si la Búsqueda cambió de nombre); sin Búsqueda la `posicion` es texto libre OBLIGATORIO."""
    busqueda_id = (fields.get("busqueda_id") or "").strip() or None
    existing_bid = (existing or {}).get("busqueda_id")
    if busqueda_id:
        busqueda = busqueda_repository.get(busqueda_id)
        if existing is not None and busqueda_id == existing_bid:
            posicion = existing.get("posicion") or (busqueda or {}).get("nombre_puesto") or ""       # mismo vínculo: el snapshot histórico se conserva
        elif busqueda is None:
            raise ValueError("La búsqueda elegida no existe.")
        else:
            posicion = busqueda["nombre_puesto"]                                                       # vínculo nuevo: se copia el puesto actual
    else:
        posicion = (fields.get("posicion") or "").strip()
        if not posicion:
            raise ValueError("Completá la posición o elegí una búsqueda.")
    canal = (fields.get("canal") or "").strip()
    if not canal:
        raise ValueError("Elegí un canal.")
    _validate_canal_publicacion_governed(canal, existing_value=(existing or {}).get("canal"))
    try:
        cantidad = int(str(fields.get("cantidad_avisos") if fields.get("cantidad_avisos") is not None else "").strip())
    except ValueError:
        cantidad = 0
    if cantidad < 1:
        raise ValueError("La cantidad de avisos tiene que ser al menos 1.")
    periodo = _validate_periodo(fields.get("periodo"), existing_value=(existing or {}).get("periodo"), today=today)
    fecha = (fields.get("fecha") or "").strip() or None
    if fecha is not None:
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            raise ValueError("La fecha no es válida.") from None
    return {"busqueda_id": busqueda_id, "posicion": posicion, "canal": canal, "cantidad_avisos": cantidad, "periodo": periodo, "fecha": fecha,
            "observacion": (fields.get("observacion") or "").strip() or None}


def create_publicacion(fields: dict) -> dict:
    return publicacion_repository.create(_shape_publicacion_fields(fields))


def update_publicacion(publicacion_id: str, expected_revision: int, fields: dict) -> dict:
    existing = publicacion_repository.get(publicacion_id)
    return publicacion_repository.update(publicacion_id, expected_revision, _shape_publicacion_fields(fields, existing=existing))


def delete_publicacion(publicacion_id: str) -> None:
    publicacion_repository.delete(publicacion_id)


def get_publicacion(publicacion_id: str) -> dict | None:
    return publicacion_repository.get(publicacion_id)


def publicacion_view(publicacion: dict) -> dict:
    """Publicación + la Búsqueda vinculada leída EN VIVO sólo para mostrar/buscar su nombre actual (la `posicion` mostrada es SIEMPRE el snapshot persistido)."""
    busqueda = busqueda_repository.get(publicacion["busqueda_id"]) if publicacion.get("busqueda_id") else None
    return {**publicacion, "busqueda": busqueda, "busqueda_nombre": (busqueda or {}).get("nombre_puesto")}


def list_publicaciones_view() -> list[dict]:
    return [publicacion_view(p) for p in publicacion_repository.list()]


def _publicacion_search_haystack(row: dict) -> str:
    """Texto sobre el que busca el campo Buscar del listado: contenido VISIBLE/relevante de la fila (posición, canal, período, fecha en ISO y DD-MM-YYYY, observación y nombre de la Búsqueda
    vinculada). Nunca ids técnicos. MISMA normalización que Candidatos/Entrevistas/Onboarding."""
    fecha = row.get("fecha") or ""
    parts = fecha.split("-")
    fecha_dmy = "-".join(reversed(parts)) if len(parts) == 3 else fecha
    values = [row.get("posicion"), row.get("canal"), row.get("periodo"), fecha, fecha_dmy, row.get("observacion"), row.get("busqueda_nombre")]
    return " ".join(_normalize_text(v) for v in values if v)


def filter_publicaciones(rows: list[dict], *, periodo: str = "", canal: str = "", q: str = "") -> list[dict]:
    """AND entre Período (igualdad exacta), Canal (igualdad exacta, incluido un valor histórico fuera de catálogo) y Buscar (substring normalizado). Un filtro vacío no restringe."""
    wanted_q = _normalize_text(q)
    return [r for r in rows
            if (not periodo or (r.get("periodo") or "") == periodo) and (not canal or (r.get("canal") or "") == canal)
            and (not wanted_q or wanted_q in _publicacion_search_haystack(r))]


def _sum_by(rows: list[dict], key) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = key(r)
        out[k] = out.get(k, 0) + int(r.get("cantidad_avisos") or 0)
    return out


def _with_pct(pairs: list[tuple[str, int]]) -> list[dict]:
    top = max((c for _, c in pairs), default=0)
    return [{"label": label, "count": count, "pct": round(100 * count / top) if top else 0} for label, count in pairs]


def publicaciones_summary(filtered_rows: list[dict], all_rows: list[dict], today: date | None = None) -> dict:
    """Resumen del listado (MISMA semántica que `PublicacionesView` de la referencia): Avisos publicados = SUMA de `cantidad_avisos` (no cuenta filas); Búsquedas difundidas = posiciones
    DISTINTAS normalizadas (trim + minúsculas), no `busqueda_id`; Registros = filas; y las dos distribuciones se calculan sobre el conjunto FILTRADO. «Este trimestre» es la única métrica que usa el
    dataset COMPLETO (suma de `cantidad_avisos` del período actual). Canal: mayor a menor, desempate alfabético; Período: cronológico descendente (más reciente primero). Los canales históricos
    fuera de catálogo se incluyen tal cual (no depende del catálogo activo)."""
    periodo_hoy = periodo_actual(today)
    por_canal = _sum_by(filtered_rows, lambda r: r.get("canal") or "—")
    por_periodo = _sum_by(filtered_rows, lambda r: r.get("periodo") or "—")
    return {
        "avisos_publicados": sum(por_canal.values()),
        "busquedas_difundidas": len({(r.get("posicion") or "").strip().lower() for r in filtered_rows}),
        "registros": len(filtered_rows),
        "periodo_actual": periodo_hoy,
        "avisos_trimestre": sum(int(r.get("cantidad_avisos") or 0) for r in all_rows if r.get("periodo") == periodo_hoy),
        "por_canal": _with_pct(sorted(por_canal.items(), key=lambda kv: (-kv[1], kv[0].lower(), kv[0]))),
        "por_periodo": _with_pct(sorted(por_periodo.items(), key=lambda kv: kv[0], reverse=True)),
    }
