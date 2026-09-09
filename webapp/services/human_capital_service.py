"""
human_capital_service.py — Capital Humano Phase 3 (First Profile Slice) +
Phase 3.2 (Profile Search, Filters & Duplicate Advisory).

Responsabilidad (§14 del ticket original, ampliada por Phase 3.2 §2/§4/§9):
`list_profiles()` + `create_profile(...)` + `filter_profiles(...)` — este
service es el ÚNICO lugar que conoce reglas de negocio de Profile (campos
requeridos, el enum de `source`, QUÉ campos son buscables, CÓMO se
normaliza texto para búsqueda/duplicados, y el contrato de duplicate
advisory — ver Legacy Profile Contract, Phase 2 §5/§6 y Phase 3.2 §3/§9) —
el router NUNCA valida, NUNCA filtra, NUNCA genera IDs, NUNCA serializa
JSON; el repository NUNCA valida negocio ni filtra, solo persiste/lista
todo lo que ya llega shapeado (Phase 3.2 §6: filtrar es una regla de
negocio sobre QUÉ campos importan y CÓMO se comparan, no un detalle de
persistencia — vive acá, nunca en `profile_repository.py`).

Validación → `ValueError` (el router lo traduce al contrato de error
`Validación` del Hub, re-mostrando el form — MISMO patrón exacto que
`wizard.py::tree_feedback_submit` con `store.submit_feedback`). Fallo de
persistencia → `profile_repository.ProfileStorageError`/
`ProfileConfigIncompleteError` se dejan propagar tal cual (el router las
traduce a los contratos `No disponible`/SOURCE_ERROR y CONFIG_INCOMPLETE
respectivamente, Phase 3.1 §5) — este service no las captura ni las
reinterpreta, para no esconder ninguna de las distinciones que el
repository ya establece. Duplicate advisory → `ProfileDuplicateAdvisory`
(Phase 3.2 §9/§22): una CUARTA situación semántica, nunca un
`ProfileStorageError` ni un `ValueError` — no es un fallo, es una pausa
deliberada que el router traduce a su propio contrato `possible_duplicate`,
nunca reutilizando `No disponible`/`feedback-error`.

SIN índice/cache (Phase 3.2 §14, explícito, mismo criterio que Phase 3
§12 para `profile_repository.list()`): `filter_profiles()` recorre en
memoria la lista YA leída por el caller — nunca vuelve a tocar disco, y
`create_profile()` solo relee vía `profile_repository.list()` cuando
efectivamente necesita chequear duplicados (nunca si
`duplicate_confirmed=True`, ver abajo)."""
from __future__ import annotations

from webapp.services import profile_repository
from webapp.services.profile_repository import (  # re-exportados para el router
    ProfileConfigIncompleteError,
    ProfileStorageError,
    VALID_SOURCES,
)

__all__ = [
    "ProfileConfigIncompleteError", "ProfileDuplicateAdvisory", "ProfileStorageError",
    "VALID_SOURCES", "list_profiles", "create_profile", "filter_profiles",
]

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

# Filtros estructurados con match EXACTO (normalizado) — faceta/categoría,
# no texto libre (Phase 3.2 §5). `location` NO está acá a propósito: ver
# `_matches_structured_filters`.
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

    `seniority`/`source`/`potential_mark`: EXACT match normalizado — son
    filtros de faceta/categoría (§5). `source` ya tiene un enum
    server-side (`VALID_SOURCES`); `seniority`/`potential_mark` no lo
    tienen todavía, pero interpretamos "filtro estructurado" igual de
    literal para los tres, exact match sobre lo que sea que esté
    guardado.

    `location` es la ÚNICA excepción: CONTAINS, no exact match (Phase 3.2
    §5, decisión explícita) — es texto libre real, tipeado a mano sin
    lista fija ni en el legacy ni acá ("Buenos Aires" / "CABA" / "Bs As"
    nunca matchean entre sí con exact match, lo que volvería el filtro
    casi inútil sobre datos reales) — MISMO tratamiento que ya le da la
    búsqueda textual libre (`q`) a este campo."""
    for field in _EXACT_MATCH_FILTER_FIELDS:
        wanted = _normalize_text(query.get(field))
        if wanted and _normalize_text(profile.get(field)) != wanted:
            return False
    location_wanted = _normalize_text(query.get("location"))
    if location_wanted and location_wanted not in _normalize_text(profile.get("location")):
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


def _find_duplicate(profile: dict, existing: list[dict]) -> dict | None:
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
    (`create_profile`) quien decide qué hacer con el resultado. Advisory,
    NUNCA uniqueness constraint (§12): la app puede legítimamente
    contener personas con el mismo nombre, emails faltantes, o incluso
    duplicados deliberados — esta función solo informa, nunca bloquea."""
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
    return profile_repository.list()


def create_profile(fields: dict, *, duplicate_confirmed: bool = False) -> dict:
    """`fields` es el input crudo del form (strings, típicamente ya
    `.strip()`-eado por el router — ver `_clean`). Levanta `ValueError`
    (mensaje listo para mostrar al usuario) si:
      - falta `first_name`/`last_name` (§3, únicos campos requeridos —
        `email` sigue siendo opcional, nunca se usa como identidad, ver
        Phase 2 §5/§8);
      - `source` viene con un valor fuera de `VALID_SOURCES` (nunca el
        mismatch UI-acepta/persistencia-rechaza del legacy — Phase 2 §5/§6);
      - `potential_rating` no es un entero 1-5 (MISMO rango que el CHECK
        legacy `banco_cvs.potencial_estrellas`, ver Phase 2 §5).

    Validación SIEMPRE corre primero — un chequeo de duplicados sobre un
    Profile todavía inválido no tendría sentido (Phase 3.2).

    `duplicate_confirmed=False` (default): si `_find_duplicate` encuentra
    un match contra los Profiles YA existentes (`profile_repository.list()`
    — una lectura adicional, solo acá, nunca si ya viene confirmado),
    levanta `ProfileDuplicateAdvisory` con el Profile existente que
    matcheó — la creación NO ocurre todavía (§9/§10). `duplicate_confirmed
    =True` (Phase 3.2 §11 — override explícito y server-side, nunca un
    framework de idempotencia/estado global): salta el chequeo por
    completo, ni siquiera relee el listado — el usuario ya decidió
    continuar deliberadamente.

    Devuelve el Profile YA persistido (incluye `profile_id`/`schema_version`/
    `created_at`/`updated_at`, agregados por el repository)."""
    first_name = (fields.get("first_name") or "").strip()
    last_name = (fields.get("last_name") or "").strip()
    if not first_name or not last_name:
        raise ValueError("Completá nombre y apellido.")

    source = (fields.get("source") or "").strip() or None
    if source is not None and source not in profile_repository.VALID_SOURCES:
        raise ValueError(f"Fuente inválida. Elegí una de: {', '.join(profile_repository.VALID_SOURCES)}.")

    potential_rating_raw = (fields.get("potential_rating") or "").strip()
    potential_rating: int | None = None
    if potential_rating_raw:
        try:
            potential_rating = int(potential_rating_raw)
        except ValueError:
            raise ValueError("El potencial en estrellas tiene que ser un número entre 1 y 5.") from None
        if not (1 <= potential_rating <= 5):
            raise ValueError("El potencial en estrellas tiene que estar entre 1 y 5.")

    profile = {
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
    if not duplicate_confirmed:
        duplicate = _find_duplicate(profile, profile_repository.list())
        if duplicate is not None:
            raise ProfileDuplicateAdvisory(duplicate)
    return profile_repository.create(profile)
