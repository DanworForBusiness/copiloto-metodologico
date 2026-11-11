"""
share_recipients.py — Selector de destinatarios CORPORATIVOS, transversal a las verticales del Hub (Compartir perfiles de Capital Humano hoy;
CRM, Operaciones, Academy y Copiloto a futuro). Independiente del mecanismo de ENVÍO.

Dos ejes separados:
  * PROVEEDOR (`SHARE_RECIPIENTS_PROVIDER`, con `HC_SHARE_RECIPIENTS_PROVIDER` como compatibilidad transitoria): cómo se buscan —
    "allowlist" (catálogo gobernado de la vertical) o "directory" (Microsoft Entra con token delegado; default).
  * CONTEXTO (vertical de la operación, lo RESUELVE el backend de cada ruta; el navegador nunca lo elige): qué usuarios son elegibles en
    modo allowlist — la allowlist de admisión de esa vertical (`CONTEXTS`). Estar en una allowlist no concede permisos sobre ningún recurso.

Nombres: Colaboradores → `Title` de la User Information List → correo (vía `ReportingRepository.get_people_names`, una lectura de cada lista,
token delegado base). Nunca amplía destinatarios ni deduce nombres del alias. Sin caché persistente.
"""
from __future__ import annotations

import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import config
from core import microsoft365

_logger = logging.getLogger("danwor.share_recipients")

GRAPH_USERS_SEARCH = ("https://graph.microsoft.com/v1.0/users?$search=%22displayName:{q}%22%20OR%20%22mail:{q}%22"
                      "&$select=id,displayName,mail,userPrincipalName&$top={top}&$count=true")
GRAPH_USER_GET = "https://graph.microsoft.com/v1.0/users/{id}?$select=id,displayName,mail,userPrincipalName"
MIN_QUERY = 3
MAX_RESULTS = 15
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SEARCH_SAFE = re.compile(r'[\\"]')                                  # se quitan comillas/backslash del término $search

# Contexto funcional → allowlist de admisión de la vertical (parser y normalización de `auth_service`). «ALL» nunca es un destinatario:
# una lista que significa «todos» (None) produce un catálogo VACÍO, nunca el directorio entero.
CONTEXT_COPILOT, CONTEXT_ACADEMY, CONTEXT_OPERATIONS, CONTEXT_COMMERCIAL, CONTEXT_HUMAN_CAPITAL = (
    "copilot", "academy", "operations", "commercial", "human-capital")


def _context_sources() -> dict:
    from webapp.services import auth_service
    return {CONTEXT_COPILOT: auth_service._allowed_set, CONTEXT_ACADEMY: auth_service._academy_set,
            CONTEXT_OPERATIONS: auth_service._operations_set, CONTEXT_COMMERCIAL: auth_service._commercial_set,
            CONTEXT_HUMAN_CAPITAL: auth_service._human_capital_set}


CONTEXTS = (CONTEXT_COPILOT, CONTEXT_ACADEMY, CONTEXT_OPERATIONS, CONTEXT_COMMERCIAL, CONTEXT_HUMAN_CAPITAL)


def context_allowlist(context: str) -> set[str]:
    """Correos normalizados de la allowlist del contexto. Contexto desconocido → `ValueError` (fail-closed; nunca otra lista)."""
    source = _context_sources().get(context)
    if source is None:
        raise ValueError(f"contexto de destinatarios desconocido: {context!r}")
    emails = source()
    return set(emails) if emails else set()                          # None («ALL») o vacío → sin destinatarios


class RecipientsError(RuntimeError):
    """Base del selector (fail-closed)."""


PROVIDER_DIRECTORY = "directory"
PROVIDER_ALLOWLIST = "allowlist"


def recipient_provider() -> str:
    """Proveedor de destinatarios EXPLÍCITO y TRANSVERSAL: `SHARE_RECIPIENTS_PROVIDER`; si no está definido, el nombre anterior
    `HC_SHARE_RECIPIENTS_PROVIDER` (COMPATIBILIDAD TRANSITORIA: retirar cuando el entorno use el nombre nuevo). Cualquier valor distinto de
    "allowlist" es el directorio: el catálogo local nunca se activa por un error de Graph ni por omisión."""
    value = str(getattr(config, "SHARE_RECIPIENTS_PROVIDER", "") or getattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", "") or "").strip().lower()
    return PROVIDER_ALLOWLIST if value == PROVIDER_ALLOWLIST else PROVIDER_DIRECTORY


def allowlist_recipients(context: str) -> list[dict]:
    """Catálogo gobernado TRANSITORIO: los usuarios de `HUMAN_CAPITAL_ALLOWED_USERS` (mismo parser/normalización que la admisión a Capital
    Humano). Solo direcciones válidas del dominio corporativo (`AUTH_DEFAULT_DOMAIN`): nada externo. Sin nombres gobernados en el Hub → el
    nombre visible es el correo."""
    domain = str(getattr(config, "AUTH_DEFAULT_DOMAIN", "") or "danwor.com").strip().lower().lstrip("@")
    # «ALL» es un valor especial del parser compartido (solo se rechaza cuando es la lista entera): mezclado en la lista se normalizaría a
    # `all@<dominio>`, que nunca es un destinatario legítimo → se excluye.
    emails = sorted(e for e in context_allowlist(context)
                    if _EMAIL_RE.match(e) and e.rsplit("@", 1)[1] == domain and e.rsplit("@", 1)[0] != "all")
    return [{"name": e, "email": e} for e in emails]


def _valid_name(value, email: str) -> str:
    """Nombre confiable para mostrar: no vacío, no es un correo ni el propio correo. Nunca se deduce del alias."""
    name = str(value or "").strip()
    return "" if not name or "@" in name or name.lower() == email else name


def roster_names(token: str) -> dict[str, str]:
    """correo (minúsculas) → nombre para PRESENTACIÓN, con prioridad: (1) lista Colaboradores del sitio Gestión (padrón de personal),
    (2) `Title` de la User Information List del mismo sitio (nombre visible sincronizado del perfil de Microsoft) cuando Colaboradores no
    tiene un nombre válido; (3) sin nombre → el llamador muestra el correo. UNA lectura de cada lista con el token DELEGADO base de lectura
    (`Sites.Read.All`, ya concedido), vía `ReportingRepository.get_people_names` (misma regla de identidad que Reporting, sin duplicarla ni
    cambiar su mapa funcional). Nunca agrega destinatarios: solo nombra a quien ya está en la allowlist. Ante cualquier falla devuelve {}."""
    if not token:
        return {}
    try:
        from webapp.services.reporting_repository import ReportingRepository
        colaboradores, user_info = ReportingRepository().get_people_names(token)
        names: dict[str, str] = {}
        for email in set(colaboradores) | set(user_info):
            chosen = _valid_name(colaboradores.get(email), email) or _valid_name(user_info.get(email), email)
            if chosen:
                names[email] = chosen
        return names
    except Exception as exc:                                         # noqa: BLE001 — presentación accesoria: nunca bloquea la selección
        _logger.info("operation=cv_share roster_names_unavailable error=%s", type(exc).__name__)
        return {}


def allowlist_catalog(context: str, token: str, *, names=roster_names) -> dict:
    """Catálogo del proveedor «allowlist» para UNA apertura del selector: SOLO las identidades de `HUMAN_CAPITAL_ALLOWED_USERS` (el padrón
    nunca agrega destinatarios), con su nombre del padrón cuando existe; si no, el correo. Se pide una vez por apertura y se filtra en memoria."""
    rows = allowlist_recipients(context)
    roster = names(token) if rows else {}
    out = [{"name": (roster.get(r["email"]) or "").strip() or r["email"], "email": r["email"]} for r in rows]
    return {"recipients": out, "names_resolved": sum(1 for r in out if r["name"] != r["email"])}


def recipients_enabled(context: str) -> bool:
    """¿Hay un proveedor de destinatarios utilizable? Directorio: scope delegado configurado. Catálogo: al menos una dirección corporativa."""
    if recipient_provider() == PROVIDER_ALLOWLIST:
        return bool(allowlist_recipients(context))
    return bool(directory_scope())


def needs_delegated_token() -> bool:
    """Solo el directorio consulta Microsoft con la identidad delegada; el catálogo gobernado se resuelve en el servidor."""
    return recipient_provider() == PROVIDER_DIRECTORY


def search_recipients(context: str, query: str, token: str) -> list[dict]:
    """Búsqueda del selector según el proveedor explícito. Misma forma de resultado ([{name, email}]) y mismo mínimo de caracteres."""
    if recipient_provider() == PROVIDER_ALLOWLIST:
        q = (query or "").strip().lower()
        if len(q) < MIN_QUERY:
            return []
        return [r for r in allowlist_recipients(context) if q in r["email"]][:MAX_RESULTS]
    return search_directory(query, token)


def directory_scope() -> str:
    """Scope delegado para buscar el directorio (`/users?$search`), leído en cada llamada. No es secreto; se expone vía la config de auth para
    que el frontend lo pida al token delegado. Vacío = búsqueda DESHABILITADA (prerequisito `User.ReadBasic.All` sin conceder)."""
    return str(getattr(config, "HC_DIRECTORY_SEARCH_SCOPE", "") or "").strip()


class DirectorySearchUnavailable(RecipientsError):
    """El directorio no se pudo consultar por permisos (403/401): prerequisito `User.ReadBasic.All` sin conceder, o token sin ese scope."""


class DirectoryError(RecipientsError):
    """Error transitorio/indeterminado consultando el directorio."""


# ───────────────────────── directorio (token DELEGADO) ─────────────────────────

def _dedup(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        key = r["email"].lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _row(u: dict) -> dict | None:
    email = str(u.get("mail") or u.get("userPrincipalName") or "").strip()
    if not email or not _EMAIL_RE.match(email):
        return None
    return {"name": str(u.get("displayName") or email).strip(), "email": email}


def search_directory(query: str, token: str, *, fetch=microsoft365.graph_get_json) -> list[dict]:
    """Usuarios corporativos que coinciden con `query` (nombre o correo), vía Graph `/users?$search` con el token DELEGADO. ≥3 caracteres.
    `DirectorySearchUnavailable` si falta permiso (403/401); `DirectoryError` ante fallo transitorio. Nunca lanza por query corta (devuelve [])."""
    if not directory_scope():
        raise DirectorySearchUnavailable("búsqueda de directorio deshabilitada: falta conceder el permiso delegado (HC_DIRECTORY_SEARCH_SCOPE vacío)")
    q = _SEARCH_SAFE.sub("", (query or "").strip())
    if len(q) < MIN_QUERY:
        return []
    url = GRAPH_USERS_SEARCH.format(q=quote(q), top=MAX_RESULTS)
    try:
        data = fetch(url, token, extra_headers={"ConsistencyLevel": "eventual"})
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise DirectorySearchUnavailable(f"sin permiso para consultar el directorio (HTTP {exc.code})") from exc
        raise DirectoryError(f"error consultando el directorio (HTTP {exc.code})") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise DirectoryError(f"no se pudo consultar el directorio ({type(exc).__name__})") from exc
    rows = [r for r in (_row(u) for u in (data.get("value") or [])) if r is not None]
    return _dedup(rows)[:MAX_RESULTS]


def validate_recipients(emails: list[str], token: str, *, context: str = "", fetch=microsoft365.graph_get_json) -> tuple[list[dict], list[str]]:
    """Valida CADA destinatario contra el proveedor explícito (el backend no confía en el autocomplete). Directorio: resuelve `/users/{email}`
    con el token DELEGADO; un 403/401 → `DirectorySearchUnavailable` (no se puede validar ⇒ no se envía). Catálogo: la dirección tiene que estar
    en el catálogo gobernado. Devuelve (válidos [{name,email}], inválidos [email])."""
    if recipient_provider() == PROVIDER_ALLOWLIST:
        return _validate_allowlist(context, emails)
    valid: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in emails or []:
        email = str(raw or "").strip()
        key = email.lower()
        if not email or not _EMAIL_RE.match(email) or key in seen:
            if email and key not in seen:
                invalid.append(email)
            seen.add(key)
            continue
        seen.add(key)
        try:
            u = fetch(GRAPH_USER_GET.format(id=quote(email, safe="@")), token)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise DirectorySearchUnavailable(f"sin permiso para validar destinatarios (HTTP {exc.code})") from exc
            invalid.append(email)                                    # 404/400/… = no es una identidad válida
            continue
        except (URLError, TimeoutError, OSError, ValueError):
            invalid.append(email)
            continue
        row = _row(u)
        valid.append(row if row is not None else {"name": email, "email": email})
    return _dedup(valid), invalid


def _validate_allowlist(context: str, emails: list[str]) -> tuple[list[dict], list[str]]:
    catalog = {r["email"]: r for r in allowlist_recipients(context)}
    valid: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in emails or []:
        email = str(raw or "").strip()
        key = email.lower()
        if not email or key in seen:
            continue
        seen.add(key)
        if key in catalog:
            valid.append(catalog[key])
        else:
            invalid.append(email)
    return valid, invalid
