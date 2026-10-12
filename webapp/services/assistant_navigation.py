"""Navegación del asistente hacia activos internos del Hub (destinos: Informe de Cliente y Catálogo de datos).

El asistente ya devuelve acciones estructuradas (``AssistantRoute`` / ``AssistantRouteAction``: botón con URL interna, ``embed`` = carga en el slot del Catálogo). Este módulo
solo agrega el RECONOCIMIENTO de una intención de navegación y la resolución del destino contra los recursos reales; el enrutador la usa antes de intentar contestar con
contenido. Extensible: cada destino es un ``NavigationTarget`` (reconoce + resuelve) en ``TARGETS``; hoy ``client_report`` y ``data_catalog``.

Garantías: las URLs salen SIEMPRE de entradas reales del Catálogo (nunca de texto del usuario); la ruta destino conserva su propio control de sesión; sin ese recurso
(cliente inexistente, ambiguo o sin flujos publicados) la respuesta lo dice explícitamente y no ofrece enlaces inventados."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from core.utils import norm_text
from webapp.services import catalog_navigation, catalog_reads
from webapp.services import client_assessment_portfolio as portfolio

MAX_OPTIONS = 12


@dataclass(frozen=True)
class NavigationResult:
    message: str
    actions: tuple[tuple, ...]                    # (etiqueta, URL interna[, modo]); modo: "embed" (por defecto: carga en el slot del Catálogo) · "tab:<pestaña>" (activa esa pestaña en la página) · "link" (navega)


@dataclass(frozen=True)
class NavigationTarget:
    key: str
    resolve: Callable[[str, str, str], NavigationResult | None]    # (pregunta, pack, módulo actual) → resultado o None si la intención no es de este destino


# ── Informe de Cliente ──────────────────────────────────────────────────────────────────────────
_REPORT_PHRASE = re.compile(r"\b(?:informe|reporte)s?(?: consolidado)?(?: de| del| para)?(?: el)? clientes?\b")
_NAV_VERB = re.compile(r"\b(?:ver|veo|vea|abrir|abre|abri|abrime|mostrar|mostra|mostrame|muestrame|muestra|dame|damelo|llevame|ir|ve|acceder|acceso|quiero ver|necesito ver)\b")
_INFORMATIONAL = ("que es ", "que es el", "como funciona", "para que sirve", "para que se usa", "que contiene", "que muestra", "que incluye", "cual es el informe", "explica")
_NAME_TAIL = re.compile(r"clientes?\s+(?:para\s+|de\s+|del\s+)?([^\s?!][^?!\n]{0,60})", re.I)
_NAME_OK = re.compile(r"^[\w][\w .&-]{0,39}$", re.U)             # solo un nombre «normal»: nada de URLs, marcado ni esquemas
_STOP_TAIL = {"el", "la", "mi", "un", "una", "este", "ese", "cliente", "clientes", "informe", "reporte", "consolidado", "hub", "catalogo"}


def _is_client_report_request(question: str) -> bool:
    q = norm_text(question).strip()
    if not _REPORT_PHRASE.search(q):
        return False
    if _NAV_VERB.search(q):
        return True
    return bool(_REPORT_PHRASE.match(q)) and not any(q.startswith(t) or f" {t}" in f" {q}" for t in _INFORMATIONAL)       # «informe de cliente Pampa» (sustantivo imperativo)


def _clean_name(raw: str) -> str:
    """Nombre pedido tal como lo escribió el usuario si es un nombre «normal»; si parece una URL/marcado/esquema devuelve "" (no se repite en la respuesta)."""
    name = raw.strip(" .,;-")
    return name if _NAME_OK.match(name) and "://" not in name else ""


def _reference_flow(mod, entries, client: str):
    """Flujo de REFERENCIA técnica para abrir el informe (la ruta se identifica por un flujo del cliente; el informe muestra TODOS los modelos del cliente, no solo ese): el
    primero por (dominio, entry_id), independiente del orden de carga y de los veredictos."""
    flows = catalog_navigation.flows_for_client(mod, entries, client)
    return min(flows, key=lambda e: (str(e.dominio).lower(), e.entry_id)) if flows else None


def _report_url(entry, pack: str) -> str:
    # origin=assistant: «← Volver» cierra el detalle y restaura el módulo (no vuelve al flujo de referencia, que el usuario no pidió)
    return f"/catalog/client/assessment?flow={quote(entry.entry_id)}&pack={quote(pack)}&origin=assistant"


def _client_report(question: str, pack: str, stem: str = "") -> NavigationResult | None:
    if not _is_client_report_request(question):
        return None
    mod, entries = catalog_reads._catalog_entries()
    clients = catalog_navigation.clients(mod, entries) if mod else []          # los MISMOS clientes del selector «Clientes» del Catálogo (con al menos un flujo navegable)

    def action(client: str) -> tuple[str, str] | None:
        ref = _reference_flow(mod, entries, client)
        return (f"Informe de Cliente · {client}", _report_url(ref, pack)) if ref else None

    q = f" {norm_text(question)} "
    hits = [c for c in clients if re.search(rf"(?<![a-z0-9]){re.escape(norm_text(c))}(?![a-z0-9])", q)]
    if len(hits) == 1:
        client = hits[0]
        n = len(portfolio.client_entries(client))                       # el MISMO universo que muestra el informe (``catalog_assets``)
        return NavigationResult(
            f"Acá está el **Informe de Cliente de {client}**: consolida el estado de sus {n} modelo{'s' if n != 1 else ''} publicado{'s' if n != 1 else ''} en el Catálogo.",
            (("Ver informe", _report_url(_reference_flow(mod, entries, client), pack)),))
    if len(hits) > 1:
        acts = tuple(a for a in (action(c) for c in hits[:MAX_OPTIONS]) if a)
        return NavigationResult("Mencionaste más de un cliente. ¿De cuál querés ver el informe?", acts)
    options = tuple(a for a in (action(c) for c in clients[:MAX_OPTIONS]) if a)
    m = _NAME_TAIL.search(question)
    tail = m.group(1).strip(" .,;-") if m else ""
    asked = _clean_name(tail)
    if tail and not asked and norm_text(tail) not in _STOP_TAIL:                  # texto raro (URL, marcado): no se repite ni se interpreta
        return NavigationResult("No encontré ese cliente en el Catálogo. Estos clientes tienen Informe de Cliente:" if options else "No encontré ese cliente en el Catálogo.", options)
    if asked and norm_text(asked) not in _STOP_TAIL:
        known_elsewhere = _known_without_report(asked, clients)
        if known_elsewhere:
            return NavigationResult(f"**{known_elsewhere}** figura en otros catálogos, pero todavía no tiene modelos publicados, así que no hay Informe de Cliente disponible.", ())
        if not options:
            return NavigationResult(f"No encontré un cliente «{asked}» y el Catálogo todavía no tiene clientes con modelos publicados.", ())
        return NavigationResult(f"No encontré un cliente «{asked}» en el Catálogo. Estos clientes tienen Informe de Cliente:", options)
    if not options:
        return NavigationResult("Todavía no hay clientes con modelos publicados en el Catálogo, así que no hay Informes de Cliente para abrir.", ())
    return NavigationResult("¿De qué cliente querés ver el Informe de Cliente?", options)


def _known_without_report(asked: str, clients: list[str]) -> str | None:
    """Cliente que figura en otro catálogo (conexiones) pero no tiene flujos publicados (no está en ``clients``). None si no se conoce en ningún lado."""
    try:
        _mod, conns = catalog_reads._catalog_architecture_connections()
        names = {str(getattr(c, "cliente", "") or "").strip() for c in conns or []} - {""}
    except Exception:  # noqa: BLE001 — la navegación nunca debe romper el routing
        return None
    target = norm_text(asked)
    have = {norm_text(c) for c in clients}
    return next((n for n in sorted(names) if norm_text(n) == target and target not in have), None)


# ── Catálogo de datos (pestaña «Catálogo» de «Herramientas de decisión») ─────────────────────────────────────────────────────────
# Vistas del selector «Catálogo de datos» de `catalog_tab.html` (un test verifica que sigan siendo las mismas). La pestaña muestra ESTE catálogo en los módulos que no tienen uno
# propio: 01 (conexiones) y 02 (seguridad) muestran otro, 05 no tiene pestaña y 00 es inmersivo (ver `module.html` / `modules.py`).
DATA_CATALOG_VIEWS = ("Modelos", "Data Products", "Líneas de negocio", "Clientes")
_NO_DATA_CATALOG_MODULES = ("00", "01", "02", "05")
_OTHER_QUALIFIERS_OK = {"datos", "data", "hub", "danwor"}                  # «catálogo de datos»; «catálogo de conexiones/seguridad/APIs…» es OTRO catálogo: no se inventa destino
_CATALOG_WORD = re.compile(r"\bcatalogos?\b(?:\s+(?:de|del)\s+([a-z0-9_]+))?")
_AVAILABILITY_WORDS = re.compile(r"\b(?:disponibles?|hay|tenemos|existen|existe|cuales|ofrece|ofrecemos|tiene)\b")
_CONCEPTUAL_START = ("que es", "que significa", "como ", "para que", "por que", "explica", "defin", "cual es la diferencia")


def _data_catalog_intent(question: str) -> str | None:
    """«open» (pide abrirlo), «info» (pregunta qué catálogos hay) o None. Las preguntas conceptuales/metodológicas («qué es…», «cómo diseñar…») no son navegación."""
    q = norm_text(question).strip().lstrip("¿¡")
    m = _CATALOG_WORD.search(q)
    if not m or (m.group(1) and m.group(1) not in _OTHER_QUALIFIERS_OK):
        return None
    if _NAV_VERB.search(q) and not q.startswith(("que es", "que significa")):
        return "open"
    if re.search(r"\bcatalogos\b", q) and _AVAILABILITY_WORDS.search(q) and not q.startswith(_CONCEPTUAL_START):
        return "info"
    return None


def _data_catalog_hosts(pack: str) -> list:
    """Módulos cuya pestaña «Catálogo» ES el Catálogo de datos, en orden."""
    from webapp.services.content_service import list_modules
    return [m for m in list_modules(pack) if str(m.number) not in _NO_DATA_CATALOG_MODULES]


def _data_catalog_views() -> list[tuple[str, int | None]]:
    """(vista, cantidad|None) de lo REALMENTE disponible hoy, con las mismas fuentes que la pestaña (nada se inventa ni se duplica)."""
    from webapp.services import domain_classification_service as dcs
    models = len(catalog_reads._catalog_model_domains())
    products = len(catalog_reads._catalog_data_products_list())
    clients = len(catalog_reads._catalog_clients())
    views: list[tuple[str, int | None]] = []
    if models:
        views.append(("Modelos", models))
    if products:
        views.append(("Data Products", products))
    if models and dcs.lob_options():
        views.append(("Líneas de negocio", None))
    if clients:
        views.append(("Clientes", clients))
    return views


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def _data_catalog(question: str, pack: str, stem: str = "") -> NavigationResult | None:
    intent = _data_catalog_intent(question)
    if intent is None:
        return None
    views = _data_catalog_views()
    hosts = _data_catalog_hosts(pack)
    if not views or not hosts:
        return NavigationResult("El Catálogo de datos todavía no tiene modelos ni Data Products publicados, así que no hay nada para abrir.", ())
    here = next((h for h in hosts if h.stem == stem), None)                                 # el módulo actual ya muestra ese catálogo: se activa la pestaña sin recargar
    url = f"/modules/{quote((here or hosts[0]).stem)}?pack={quote(pack)}&tab=catalogo"        # mecanismo existente `?tab=catalogo`; sin él (otro módulo) se navega a un módulo que lo muestra
    action = ("Abrir Catálogo", url, "tab:catalogo") if here else ("Abrir Catálogo", url, "link")
    if intent == "info":
        listed = _join([f"**{v}**" + (f" ({n})" if n else "") for v, n in views])
        message = f"El **Catálogo de datos** del Hub se explora en {len(views)} vistas: {listed}."
    else:
        message = f"Acá tenés el **Catálogo de datos** del Hub: explorá {_join([f'**{v}**' for v, _n in views])}."
    return NavigationResult(message, (action,))


TARGETS: tuple[NavigationTarget, ...] = (NavigationTarget("client_report", _client_report), NavigationTarget("data_catalog", _data_catalog))


def resolve(question: str, pack: str, stem: str = "") -> NavigationResult | None:
    """Primer destino que reconoce la intención de navegación; None si la pregunta no es de navegación (sigue el flujo normal del asistente)."""
    for target in TARGETS:
        try:
            result = target.resolve(question, pack, stem)
        except Exception:  # noqa: BLE001 — un fallo de navegación nunca rompe el asistente: cae al flujo normal
            result = None
        if result is not None:
            return result
    return None
