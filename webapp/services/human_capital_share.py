"""Compartir perfiles de candidatos (CCHH → Candidatos → «Compartir perfiles»): reglas puras, sin I/O propia.

Resuelve los ``profile_id`` elegidos en el listado a ``{nombre, CVs asociados}`` y entrega los textos por defecto del mensaje. Es una AYUDA para redactar (igual que «Contactar al candidato» del
Pipeline): nada se envía ni se registra desde acá; «Copiar mensaje» y «Abrir Outlook» (`mailto:`) son acciones explícitas del usuario en su navegador.

DESTINO DE LOS ENLACES: el CV físico en SharePoint. La URL sale SOLO de la metadata ya guardada de cada ProfileDocument (``web_url``, el ``webUrl`` que devolvió Graph al subir/adoptar el archivo):
nunca se construye a partir del nombre del archivo ni se infiere una ruta, no se crea ningún enlace anónimo, no se tocan permisos ni se duplica nada —quien reciba el enlace lo abre con SUS permisos
de SharePoint—. ``web_url`` es metadata de conveniencia (no es parte de la identidad del documento): si el archivo se movió o renombró en SharePoint, el enlace guardado puede quedar viejo.
Solo se admiten URLs ``https://*.sharepoint.com`` sin credenciales (misma regla que «Ver CV original» de Históricos); cualquier otra se trata como «sin enlace disponible».

VARIOS CVs POR CANDIDATO: el modelo no marca ninguno como principal (no hay «vigente») ⇒ nunca se elige uno por cuenta propia: con más de un documento, el usuario elige cuál compartir (o lo omite).
Con un único CV con enlace válido no hay ambigüedad. Sin CV, o con CV sin enlace válido, se informa y el candidato no se incluye en el mensaje.

Contrato de privacidad: el mensaje lleva SOLO nombre completo y enlace al CV. Jamás email, teléfono ni notas. El nombre del archivo solo se muestra en el diálogo (para elegir entre varios CVs), no en el mensaje."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

MAX_SHARE_PROFILES = 25                              # límite inicial acordado (la validación autoritativa es ESTA, no la del navegador)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")         # forma de un profile_id: lo que no la cumple se descarta sin consultar nada
MAX_URL_LEN = 2048
_UNSAFE_URL_CHARS = re.compile(r"[\s\x00-\x1f\x7f<>\"'`\\]")
_LABEL_MAX = 80

DEFAULT_SUBJECT = "Perfiles de candidatos | Capital Humano"
INTRO = "Hola, te comparto los siguientes perfiles de candidatos (los enlaces abren el CV en SharePoint con tus permisos):"
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


def safe_cv_url(value) -> str | None:
    """URL del CV apta para enlazar, o None. Solo ``https://*.sharepoint.com`` sin credenciales, sin espacios/controles/comillas/``<>`` y de largo acotado: nada guardado se enlaza sin validar.
    Misma regla de host que ``cv_history_review._safe_web_url`` (un test fija que no divergen)."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL_LEN or _UNSAFE_URL_CHARS.search(url):
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme != "https" or not host.endswith(".sharepoint.com") or len(host) <= len(".sharepoint.com") or parsed.username or parsed.password:
        return None
    return url


def full_name(profile: dict) -> str:
    name = f"{str(profile.get('first_name') or '').strip()} {str(profile.get('last_name') or '').strip()}".strip()
    return name or "Candidato sin nombre"


def _label(document: dict) -> str:
    """Rótulo para ELEGIR entre varios CVs: nombre del archivo y fecha de carga. Solo se muestra en el diálogo."""
    filename = str(document.get("filename") or "").strip() or "CV"
    filename = filename if len(filename) <= _LABEL_MAX else filename[: _LABEL_MAX - 1] + "…"
    uploaded = str(document.get("uploaded_at") or "")[:10]
    return f"{filename} · {uploaded}" if re.match(r"^\d{4}-\d{2}-\d{2}$", uploaded) else filename


def share_documents(documents: list[dict]) -> list[dict]:
    """CVs asociados al candidato, del más reciente al más antiguo, como ``{"id", "label", "url"}`` (``url`` None si no hay enlace válido). No elige ninguno."""
    ordered = sorted(documents or [], key=lambda d: (str(d.get("uploaded_at") or ""), str(d.get("document_id") or "")), reverse=True)
    return [{"id": str(d.get("document_id") or ""), "label": _label(d), "url": safe_cv_url(d.get("web_url"))} for d in ordered if d.get("document_id")]


@dataclass
class ShareSelection:
    items: list[dict] = field(default_factory=list)          # [{"id", "name", "docs": [{"id", "label", "url"}]}] en el orden de la selección
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


def resolve(raw_ids, get_profile: Callable[[str], dict | None], list_documents: Callable[[str], list[dict]]) -> ShareSelection:
    """Resuelve la selección contra el modelo vigente. El límite se aplica ANTES de consultar ningún perfil (más de ``MAX_SHARE_PROFILES`` ⇒ ``over_limit`` sin leer nada). Un perfil inexistente o
    dado de baja (``deleted_at``) es «no disponible»: no se incluye ni se revela nada de él, y sus documentos ni se consultan."""
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
        selection.items.append({"id": profile_id, "name": full_name(profile), "docs": share_documents(list_documents(profile_id))})
    if not selection.items:
        selection.reason = REASON_NONE_AVAILABLE
    return selection
