"""
human_capital_service.py — Capital Humano Phase 3 (First Profile Slice).

Responsabilidad mínima (§14 del ticket): `list_profiles()` +
`create_profile(...)`. Este service es el ÚNICO lugar que conoce reglas de
negocio de Profile (campos requeridos, el enum de `source` — ver
Legacy Profile Contract, Phase 2 §5/§6) — el router NUNCA valida, NUNCA
genera IDs, NUNCA serializa JSON; el repository NUNCA valida negocio, solo
persiste lo que ya llega shapeado.

Validación → `ValueError` (el router lo traduce al contrato de error
`Validación` del Hub, re-mostrando el form — MISMO patrón exacto que
`wizard.py::tree_feedback_submit` con `store.submit_feedback`). Fallo de
persistencia → `profile_repository.ProfileStorageError` se deja propagar
tal cual (el router lo traduce al contrato `No disponible`) — este service
no la captura ni la reinterpreta, para no esconder la distinción entre
"fuente vacía" y "fuente inaccesible" que el repository ya establece."""
from __future__ import annotations

from webapp.services import profile_repository
from webapp.services.profile_repository import ProfileStorageError, VALID_SOURCES  # re-exportados para el router

__all__ = ["ProfileStorageError", "VALID_SOURCES", "list_profiles", "create_profile"]

_REQUIRED_FIELDS = ("first_name", "last_name")
_OPTIONAL_FIELDS = (
    "email", "phone", "linkedin_url", "location", "profile_skills",
    "seniority", "source", "tags", "notes", "potential_rating", "potential_mark",
)


def list_profiles() -> list[dict]:
    return profile_repository.list()


def create_profile(fields: dict) -> dict:
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
    return profile_repository.create(profile)
