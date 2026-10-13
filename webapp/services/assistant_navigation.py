"""Navegación del asistente hacia activos internos del Hub (destinos: Informe de Cliente y Catálogo de datos).

El asistente ya devuelve acciones estructuradas (``AssistantRoute`` / ``AssistantRouteAction``: botón con URL interna, ``embed`` = carga en el slot del Catálogo). Este módulo
solo agrega el RECONOCIMIENTO de una intención de navegación y la resolución del destino contra los recursos reales; el enrutador la usa antes de intentar contestar con
contenido. Extensible: cada destino es un ``NavigationTarget`` (reconoce + resuelve) en ``TARGETS``; hoy ``client_report`` y ``data_catalog``.

Garantías: las URLs salen SIEMPRE de entradas reales del Catálogo (nunca de texto del usuario); la ruta destino conserva su propio control de sesión; sin ese recurso
(cliente inexistente, ambiguo o sin flujos publicados) la respuesta lo dice explícitamente y no ofrece enlaces inventados."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from core.utils import norm_text
from webapp.services import assistant_capabilities as caps
from webapp.services import catalog_navigation, catalog_reads
from webapp.services import client_assessment_portfolio as portfolio
from webapp.services import domain_classification_service as dcs

MAX_OPTIONS = 12
log = logging.getLogger("danwor.assistant_navigation")


@dataclass(frozen=True)
class NavigationResult:
    message: str
    actions: tuple[tuple, ...]                    # (etiqueta, URL interna[, modo]); modo: "embed" (por defecto: carga en el slot del Catálogo) · "tab:<pestaña>" (activa esa pestaña en la página) · "link" (navega)
    result: caps.ResultSet | None = None          # resultado estructurado de una consulta sobre el Catálogo (filas navegables); ``message`` queda como su encabezado


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
_OTHER_QUALIFIERS_OK = {"datos", "data", "hub", "danwor", "modelos", "dominios"}                # «catálogo de datos»; «catálogo de conexiones/seguridad/APIs…» es OTRO catálogo: no se inventa destino
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


def _open_catalog_action(pack: str, stem: str = "") -> tuple | None:
    """Acción «Abrir Catálogo»: activa la pestaña en el módulo actual si ya muestra el Catálogo de datos; si no, navega al primer módulo que lo muestra. None si ninguno lo hospeda."""
    hosts = _data_catalog_hosts(pack)
    if not hosts:
        return None
    here = next((h for h in hosts if h.stem == stem), None)                                 # el módulo actual ya muestra ese catálogo: se activa la pestaña sin recargar
    url = f"/modules/{quote((here or hosts[0]).stem)}?pack={quote(pack)}&tab=catalogo"        # mecanismo existente `?tab=catalogo`; sin él (otro módulo) se navega a un módulo que lo muestra
    return ("Abrir Catálogo", url, "tab:catalogo") if here else ("Abrir Catálogo", url, "link")


def _data_catalog(question: str, pack: str, stem: str = "") -> NavigationResult | None:
    intent = _data_catalog_intent(question)
    if intent is None:
        return None
    views = _data_catalog_views()
    action = _open_catalog_action(pack, stem)
    if not views or action is None:
        return NavigationResult("El Catálogo de datos todavía no tiene modelos ni Data Products publicados, así que no hay nada para abrir.", ())
    if intent == "info":
        listed = _join([f"**{v}**" + (f" ({n})" if n else "") for v, n in views])
        message = f"El **Catálogo de datos** del Hub se explora en {len(views)} vistas: {listed}."
    else:
        message = f"Acá tenés el **Catálogo de datos** del Hub: explorá {_join([f'**{v}**' for v, _n in views])}."
    return NavigationResult(message, (action,))


# ── Inventario del Catálogo: resumen agregado, listados acotados y filtros ─────────────────────────────────────────────────────
# El asistente RESUME, ayuda a acotar y lleva al Catálogo; no vuelca el inventario en la conversación (con 100+ modelos no escalaría). Mismas fuentes que el Catálogo: el universo es
# `catalog_navigation.browsable_flows` (el que parte la vista «Líneas de negocio» y recorre «Clientes») y la clasificación es la EFECTIVA de `dcs.effective_lob_by_entry` (manual > automática
# HIGH > sin clasificar): no hay inventario ni clasificación propios. Un modelo sin clasificación efectiva jamás se presenta bajo una línea de negocio.
MAX_LIST = 10                                                                  # tope de modelos listados por respuesta
_INV_SUBJECT = re.compile(r"\b(?:dominios?|modelos?)\b")
_INV_CUE = re.compile(r"\b(?:hay|tenemos|tienen|disponibles?|existen|existe|cuantos|cuantas|cuales|contamos)\b")
_INV_LIST_VERB = re.compile(r"\b(?:lista|listame|listar|listado|enumera|enumerame|cuales son)\b")
_INV_TIENE = re.compile(r"\btiene(?:n)?\b")
_INV_COUNT = re.compile(r"\b(\d{1,4})\s+(?:\w+\s+)?(?:modelos?|dominios?)\b|\b(?:primeros?|top)\s+(\d{1,4})\b")
_INV_UNCLASSIFIED = re.compile(r"\b(?:sin clasificar|no clasificados?|sin clasificacion)\b")
# preguntas metodológicas/conceptuales y otros inventarios (Data Products, conexiones, CDS, APIs, seguridad): no son este destino
_INV_METHOD = re.compile(r"\b(?:definir|defino|definen|disenar|diseno|crear|armar|construir|modelar|conviene|deberia|diferencias?|practicas?|metodolog\w*|estandar\w*|gobernar|gobierno|tipicos?|comunes|habituales|ejemplos?)\b")
_INV_OTHER = re.compile(r"\b(?:data products?|productos? de datos|conexion\w*|cds|apis?|seguridad|usuarios?)\b")


@dataclass(frozen=True)
class _InventoryIntent:
    mode: str                  # "summary" (pregunta amplia) | "list" (listado explícito o filtrado)
    limit: int                 # tope de modelos a listar (≤ MAX_LIST)
    capped: bool               # pidió más que el tope
    lob: str | None            # id de línea de negocio, "UNCLASSIFIED" o None
    client: str | None         # cliente del Catálogo (nombre canónico) o None


def _word(term: str, text: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(norm_text(term))}(?![a-z0-9])", text))


def is_methodological(question: str) -> bool:
    """Pregunta conceptual/metodológica («qué es…», «cómo definir…», «dominios típicos…»): se contesta con el contenido, nunca con un inventario del Catálogo."""
    q = norm_text(question).strip().lstrip("¿¡")
    return q.startswith(_CONCEPTUAL_START) or bool(_INV_METHOD.search(q))


def _lobs_named(q: str) -> list[str]:
    """Ids de línea de negocio (taxonomía gobernada) o «UNCLASSIFIED» nombrados en la consulta ya normalizada."""
    from webapp.services import domain_classification_service as dcs
    if _INV_UNCLASSIFIED.search(q):
        return [dcs.UNCLASSIFIED]
    padded = f" {q} "
    return [o["id"] for o in dcs.lob_options() if o["id"] != dcs.UNCLASSIFIED and _word(o["label"], padded)]


def names_line_of_business(question: str) -> bool:
    """La consulta nombra una línea de negocio (p. ej. «Ventas»). Esa pregunta la responde la clasificación EFECTIVA del Catálogo; una ruta anterior que empareja por el rótulo de dominio legado
    («Ventas» como dominio ≠ línea de negocio) no puede contestarla."""
    return bool(_lobs_named(norm_text(question).strip().lstrip("¿¡")))


def _inventory_intent(question: str, clients: list[str]) -> _InventoryIntent | None:
    q = norm_text(question).strip().lstrip("¿¡")
    if is_methodological(question) or _INV_OTHER.search(q):
        return None
    padded = f" {q} "
    hit_clients = [c for c in clients if _word(c, padded)]
    lobs = _lobs_named(q)
    if len(hit_clients) > 1 or len(lobs) > 1:
        return None                                                                # ambiguo: no se elige por el usuario
    client, lob = (hit_clients[0] if hit_clients else None), (lobs[0] if lobs else None)
    count = _INV_COUNT.search(q)
    n = int(next(g for g in count.groups() if g)) if count else 0
    list_verb = bool(_NAV_VERB.search(q) or _INV_LIST_VERB.search(q))
    # «catálogos de Ventas / de Pampa» es un inventario filtrado como «modelos de Ventas / de Pampa» (un «catálogo» a secas lo resuelve `data_catalog`)
    subject = bool(_INV_SUBJECT.search(q)) or bool((lob or client) and re.search(r"\bcatalogos?\b", q))
    if subject:
        if not (_INV_CUE.search(q) or list_verb or n or lob or client):
            return None
    elif not ((client and _INV_TIENE.search(q)) or (lob and _INV_CUE.search(q))):  # sin sujeto: «¿qué tiene Pampa?» (cliente conocido) o «¿tenemos algo de Ventas?»
        return None
    limit = min(n, MAX_LIST) if n else MAX_LIST
    mode = "list" if (lob or client or list_verb or n) else "summary"
    return _InventoryIntent(mode, limit, n > MAX_LIST, lob, client)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _entries(n: int) -> str:
    """«35 entradas navegables»: la unidad es la entrada del Catálogo (un flujo/modelo publicado), la misma que particiona «Líneas de negocio» y recorre «Clientes»; no «modelos de datos» ni «dominios»."""
    return f"{n} entrada{'s' if n != 1 else ''} navegable{'s' if n != 1 else ''}"


def _distribution_text(aggregates: tuple[tuple[str, str, int], ...]) -> str:
    """«Ventas: 18 · Cadena de suministro: 3 · **Sin clasificar**: 12» a partir de la distribución del ``ResultSet`` (sin clasificar y no disponible, resaltados).
    Formato «etiqueta: n» y no «etiqueta (n)»: el render del chat (``render_llm_html``) interpreta «n)» como inicio de lista numerada."""
    return " · ".join(f"**{label}**: {n}" if group in (dcs.UNCLASSIFIED, dcs.UNAVAILABLE) else f"{label}: {n}" for group, label, n in aggregates)


def _catalog_inventory(question: str, pack: str, stem: str = "") -> NavigationResult | None:
    mod, entries = catalog_reads._catalog_entries()
    intent = _inventory_intent(question, catalog_navigation.clients(mod, entries) if mod else [])
    return _inventory_result(intent, pack, stem) if intent is not None else None


def inventory_summary(pack: str, stem: str = "") -> NavigationResult | None:
    """Resumen agregado del Catálogo para las preguntas amplias que reconoce el enrutador (p. ej. «¿qué flujos hay implementados?»); la misma respuesta acotada que el destino ``catalog_inventory``."""
    return _inventory_result(_InventoryIntent("summary", MAX_LIST, False, None, None), pack, stem)


def _inventory_result(intent: _InventoryIntent, pack: str, stem: str) -> NavigationResult:
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    clients = catalog_navigation.clients(mod, entries) if mod else []
    if not flows:
        return NavigationResult("El Catálogo todavía no tiene entradas navegables.", ())
    open_action = _open_catalog_action(pack, stem)
    actions: list[tuple] = [open_action] if open_action else []
    labels = caps.lob_labels()
    governed = caps.governed_read(flows)                                               # estados + clasificación efectiva, UNA lectura por lote (None = no disponible)
    eff = governed.eff if governed is not None else None
    scope = flows
    if intent.client:
        scope = catalog_navigation.flows_for_client(mod, entries, intent.client)
        ref = _reference_flow(mod, entries, intent.client)
        if ref:
            actions.append((f"Informe de Cliente · {intent.client}", _report_url(ref, pack)))
    if intent.lob:
        if eff is None:
            return NavigationResult("No pude leer la clasificación por línea de negocio en este momento, así que no filtro por línea de negocio para no darte un dato incorrecto. Probá de nuevo en unos segundos o abrí el Catálogo.", tuple(actions[:1]))
        scope = [e for e in scope if eff[e.entry_id]["group"] == intent.lob]            # SOLO la clasificación efectiva: lo que no la tiene no se cuela bajo ninguna línea

    if intent.mode == "summary":
        aggregates = caps.distribution(flows, eff, labels) if eff is not None else ()
        result = caps.ResultSet("summary", total=len(flows), catalog=caps.CatalogSummary(aggregates=aggregates, classified=eff is not None))
        lines = [f"El Catálogo contiene **{_entries(result.total)}** de **{_plural(len(clients), 'cliente')}**."]
        if result.catalog.classified:
            lines += ["", f"- **Por línea de negocio** (clasificación efectiva): {_distribution_text(result.catalog.aggregates)}"]
        else:
            lines += ["", "- La clasificación por línea de negocio no está disponible en este momento."]
        products = len(catalog_reads._catalog_data_products_list())
        if products:
            lines.append(f"- Hay **{_plural(products, 'Data Product')}**: son otra vista del Catálogo y no se cuentan acá.")
        top_lob = next((label for group, label, _n in result.catalog.aggregates if group not in (dcs.UNCLASSIFIED, dcs.UNAVAILABLE)), None)      # ejemplos reales: la línea con más entradas y el cliente con más entradas
        top_client = max(clients, key=lambda c: (len(catalog_navigation.flows_for_client(mod, entries, c)), c.lower()), default=None)
        tips = [f"«modelos de {top_lob}»" if top_lob else "", f"«qué tiene {top_client}»" if top_client else "", "«mostrame 10 modelos»"]
        lines += ["", "---", "", f"Para acotar: {_join([t for t in tips if t])}. O abrí el Catálogo para explorarlos completos."]       # «---»: el render del chat no separa párrafos por línea en blanco
        return NavigationResult("\n".join(lines), tuple(actions), result)

    desc = []
    if intent.client:
        desc.append(f"de **{intent.client}**")
    if intent.lob == dcs.UNCLASSIFIED:
        desc.append("**sin clasificar** (sin línea de negocio efectiva)")
    elif intent.lob:
        desc.append(f"en la línea de negocio **{labels[intent.lob]}** (clasificación efectiva)")
    where = " ".join(desc) or "en el Catálogo"
    ordered = caps.order_entries(scope)
    if not ordered:
        return NavigationResult(f"No hay entradas navegables {where}.", tuple(actions))
    shown = ordered[:intent.limit]
    head = f"Hay **{_entries(len(ordered))}** {where}"
    order_note = "ordenados por mirada Danwor (plena → parcial) y luego por cliente y nombre"
    if len(ordered) > len(shown):
        head += (f". Muestro una selección de {len(shown)} (el máximo por respuesta), {order_note}:" if intent.capped else f". Muestro {len(shown)}, {order_note}:")        # «selección»/«muestro», no «top»: no hay un ranking real
    else:
        head += f", {order_note}:" if len(ordered) > 1 else ":"
    footer = (f"Hay {len(ordered) - len(shown)} más. Para acotar, sumá un cliente o una línea de negocio, o abrí el Catálogo para explorar el resto."
              if len(ordered) > len(shown) else "")
    result = caps.ResultSet("list", total=len(ordered), items=caps.build_items(shown, governed, pack), footer=footer, catalog=caps.CatalogSummary(classified=eff is not None))
    return NavigationResult(head, tuple(actions), result)


TARGETS: tuple[NavigationTarget, ...] = (NavigationTarget("client_report", _client_report), NavigationTarget("data_catalog", _data_catalog),
                                         NavigationTarget("catalog_inventory", _catalog_inventory))


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
