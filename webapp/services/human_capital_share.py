"""Compartir perfiles de candidatos (CCHH → Candidatos → «Compartir perfiles»): reglas puras, sin I/O propia.

Resuelve los ``profile_id`` elegidos en el listado a ``{nombre, ruta de la ficha}`` y entrega los textos por defecto del mensaje. Es una AYUDA para redactar (igual que «Contactar al
candidato» del Pipeline): nada se envía ni se registra desde acá; «Copiar» y «Abrir Outlook» (`mailto:`) son acciones explícitas del usuario en su navegador.

Contrato de privacidad: el mensaje lleva SOLO nombre completo y el enlace a la ficha de consulta del Hub (`/cchh/candidatos/{id}`). Jamás email, teléfono, notas ni enlaces a SharePoint,
y nunca la pantalla de edición (`/editar`). Los enlaces son internos: abrirlos exige sesión con autorización de Capital Humano; no se crea ningún enlace anónimo ni se toca ningún permiso.
El navegador convierte la ruta en URL absoluta con su propio origen (no hay `PUBLIC_BASE_URL`, y detrás del proxy `request.base_url` puede devolver `http`)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote

MAX_SHARE_PROFILES = 25                              # límite inicial acordado (la validación autoritativa es ESTA, no la del navegador)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")         # forma de un profile_id: lo que no la cumple se descarta sin consultar nada

DEFAULT_SUBJECT = "Perfiles de candidatos | Capital Humano"
INTRO = "Hola, te comparto los siguientes perfiles de candidatos para que los revises en el Hub de Danwor (el acceso requiere autorización de Capital Humano):"
OUTRO = "Quedo atento/a a tus comentarios.\nSaludos."

REASON_EMPTY = "empty"
REASON_OVER_LIMIT = "over_limit"
REASON_NONE_AVAILABLE = "none_available"
REASON_SOURCE_ERROR = "source_error"
REASON_MESSAGES = {
    REASON_EMPTY: "No hay candidatos seleccionados para compartir.",
    REASON_OVER_LIMIT: f"Se pueden compartir hasta {MAX_SHARE_PROFILES} perfiles por vez. Quitá algunos de la selección y volvé a intentar.",
    REASON_NONE_AVAILABLE: "Ninguno de los candidatos seleccionados está disponible (puede que se hayan dado de baja).",
    REASON_SOURCE_ERROR: "No se pudo leer el Banco de CVs en este momento. Reintentá en unos instantes.",
}


def profile_path(profile_id: str) -> str:
    """Ruta ESTABLE de la ficha de consulta. Sin `return_to` ni parámetros: es el enlace que se comparte."""
    return "/cchh/candidatos/" + quote(str(profile_id), safe="")


def full_name(profile: dict) -> str:
    name = f"{str(profile.get('first_name') or '').strip()} {str(profile.get('last_name') or '').strip()}".strip()
    return name or "Candidato sin nombre"


@dataclass
class ShareSelection:
    items: list[dict] = field(default_factory=list)          # [{"id", "name", "path"}] en el orden de la selección
    unavailable: list[str] = field(default_factory=list)     # ids bien formados que ya no existen / están dados de baja
    invalid: int = 0                                         # entradas mal formadas (se ignoran)
    requested: int = 0                                       # ids únicos y bien formados que se pidieron
    reason: str | None = None                                # None = se puede armar el mensaje

    @property
    def ok(self) -> bool:
        return self.reason is None

    @property
    def message(self) -> str:
        return REASON_MESSAGES.get(self.reason or "", "")


def normalize_ids(raw) -> tuple[list[str], int]:
    """ids únicos, bien formados y en orden de aparición + cuántas entradas se descartaron por mal formadas (los duplicados no cuentan como inválidos)."""
    seen: set[str] = set()
    ids: list[str] = []
    invalid = 0
    for value in raw or []:
        candidate = str(value or "").strip()
        if not ID_RE.match(candidate):
            invalid += 1
            continue
        if candidate not in seen:
            seen.add(candidate)
            ids.append(candidate)
    return ids, invalid


def resolve(raw_ids, get_profile: Callable[[str], dict | None]) -> ShareSelection:
    """Resuelve la selección contra el modelo vigente. El límite se aplica ANTES de consultar ningún perfil (más de ``MAX_SHARE_PROFILES`` ⇒ ``over_limit`` sin leer nada). Un perfil
    inexistente o dado de baja (``deleted_at``) es «no disponible»: no se incluye ni se revela nada de él."""
    ids, invalid = normalize_ids(raw_ids)
    selection = ShareSelection(invalid=invalid, requested=len(ids))
    if not ids:
        selection.reason = REASON_EMPTY
        return selection
    if len(ids) > MAX_SHARE_PROFILES:
        selection.reason = REASON_OVER_LIMIT
        return selection
    for profile_id in ids:
        profile = get_profile(profile_id)
        if not profile or profile.get("deleted_at"):
            selection.unavailable.append(profile_id)
            continue
        selection.items.append({"id": profile_id, "name": full_name(profile), "path": profile_path(profile_id)})
    if not selection.items:
        selection.reason = REASON_NONE_AVAILABLE
    return selection
