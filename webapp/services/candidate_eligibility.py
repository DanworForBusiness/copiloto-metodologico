"""candidate_eligibility.py — regla TRANSVERSAL de elegibilidad de un candidato del Banco para NUEVAS postulaciones (puro, sin I/O).

Hoy hay un único motivo de bloqueo: la marca de potencial «No reconsiderar». Se identifica por una CLAVE SEMÁNTICA estable (`no_reconsiderar`) y no por el texto visible del catálogo `potencial` (que se gobierna desde el
workbook y puede variar en mayúsculas, acentos, espacios o signos). Todos los puntos de entrada (selector de «Agregar candidato», incorporación en el servidor y, a futuro, Sugeridos) deben consultar `blocked_reason`: así
la regla vive en un solo lugar.

Alcance: SOLO nuevas postulaciones. Las postulaciones existentes y su historial no se tocan (nunca hay bajas automáticas) y el candidato sigue visible en el Banco.

La resolución valor-de-catálogo -> clave usa `_KEY_BY_NORMALIZED_VALUE`: para sumar sinónimos o un nuevo motivo de bloqueo se agrega ahí (y, si el workbook incorporara una columna de clave, `potencial_key` sería el único lugar a extender)."""
from __future__ import annotations

import re
import unicodedata

NO_RECONSIDERAR = "no_reconsiderar"
BLOCKING_KEYS = (NO_RECONSIDERAR,)

BLOCKED_MESSAGES = {
    NO_RECONSIDERAR: "Este candidato está marcado «No reconsiderar»: no puede incorporarse a nuevos procesos. Sus postulaciones anteriores se conservan.",
}


def _normalize(value) -> str:
    """Minúsculas, sin acentos y solo letras/dígitos: «No reconsiderar», «NO  RECONSIDERAR.» y «no-reconsiderar» dan lo mismo."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


_KEY_BY_NORMALIZED_VALUE = {_normalize("No reconsiderar"): NO_RECONSIDERAR}


def potencial_key(mark) -> str | None:
    """Clave semántica de una marca de potencial (valor del catálogo `potencial`) o `None` si no tiene un significado especial."""
    return _KEY_BY_NORMALIZED_VALUE.get(_normalize(mark))


def blocked_reason(profile: dict | None) -> str | None:
    """Clave del motivo por el que el perfil NO puede recibir nuevas postulaciones, o `None` si es elegible. Un perfil inexistente o eliminado se resuelve aparte (no es este bloqueo)."""
    key = potencial_key((profile or {}).get("potential_mark"))
    return key if key in BLOCKING_KEYS else None


def is_eligible(profile: dict | None) -> bool:
    return blocked_reason(profile) is None


def message_for(reason: str | None) -> str:
    return BLOCKED_MESSAGES.get(reason or "", "")


def catalog_exposes_blocking_key(catalog_values) -> bool:
    """¿El catálogo `potencial` vigente contiene un valor que resuelve a la clave de bloqueo? Chequeo de salud: si el workbook renombrara el valor a algo irreconocible, la regla dejaría de aplicarse en silencio."""
    return any(potencial_key(v) == NO_RECONSIDERAR for v in (catalog_values or []))
