"""Navegación del asistente hacia activos internos del Hub (destinos: Informe de Cliente y Catálogo de datos).

El asistente ya devuelve acciones estructuradas (``AssistantRoute`` / ``AssistantRouteAction``: botón con URL interna, ``embed`` = carga en el slot del Catálogo). Este módulo
solo agrega el RECONOCIMIENTO de una intención de navegación y la resolución del destino contra los recursos reales; el enrutador la usa antes de intentar contestar con
contenido. Extensible: cada destino es un ``NavigationTarget`` (reconoce + resuelve) en ``TARGETS``; hoy ``client_report`` y ``data_catalog``.

Garantías: las URLs salen SIEMPRE de entradas reales del Catálogo (nunca de texto del usuario); la ruta destino conserva su propio control de sesión; sin ese recurso
(cliente inexistente, ambiguo o sin flujos publicados) la respuesta lo dice explícitamente y no ofrece enlaces inventados."""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from core.utils import norm_text
from webapp.services import assessment_presenter as presenter
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_context as qctx
from webapp.services import assistant_entry_answers as answers
from webapp.services import assistant_entry_facts as facts
from webapp.services import assistant_entry_resolver as resolver
from webapp.services import assistant_indicators as indicators
from webapp.services import assistant_query as query
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
    state: qctx.QueryDraft | None = None          # contexto de la consulta para los seguimientos (el borde HTTP lo asocia a usuario e instante); None = esta respuesta no deja contexto
    keep_state: bool = False                      # sin `state`: conservar el contexto vigente (p. ej. «abrí el segundo» no cambia la lista); por defecto una respuesta que no es una consulta lo borra


@dataclass(frozen=True)
class NavigationTarget:
    key: str
    resolve: Callable[..., NavigationResult | None]    # (pregunta, pack, módulo actual, contexto de la conversación) → resultado o None si la intención no es de este destino


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


def _client_report(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
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


def _data_catalog(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
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


# ── Assessment de una entrada: navegación al Assessment EXISTENTE ────────────────────────────────────────────────────────────────────────────
# «Ver assessment de <modelo>» se resuelve con NAVEGACIÓN: el informe vigente (o su estado, si todavía no se evaluó) lo muestra la ruta de siempre, `GET /catalog/flow/assessment` (solo lectura, con su propio control
# de sesión). El asistente no describe un assessment: contestar con un resumen sin los datos del Assessment era la causa de presentar el veredicto legado como si fuera el Juicio vigente. El modelo se identifica por
# su DOMINIO (el nombre que muestra el Catálogo) con el emparejador existente del Catálogo (`catalog_service.best_domain_match`), acotado al cliente si lo nombró; si más de un modelo coincide, se pregunta cuál
# (nunca se elige uno por el usuario). La URL sale SIEMPRE del `entry_id` real. Sin modelo identificable devuelve None: sigue el flujo normal (el LLM ya no recibe datos de evaluación).
_ASSESSMENT_WORD = re.compile(r"\bassessments?\b")
_NAME_STOP = {"the", "and", "del", "los", "las", "con", "por", "para", "from", "assessment", "assessments"}


def _name_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", norm_text(text)) if len(w) >= 3} - _NAME_STOP


def _is_assessment_request(q: str) -> bool:
    """Pide ver/abrir/consultar el assessment de algo (verbo de navegación, forma de disponibilidad o el sustantivo como imperativo). Lo conceptual («qué es…», «cómo se genera…») no es navegación."""
    if not _ASSESSMENT_WORD.search(q) or is_methodological(q):
        return False
    return bool(_NAV_VERB.search(q) or _AVAILABILITY_WORDS.search(q) or re.match(r"^(?:el |los )?assessments?\b", q))


def _assessment_candidates(q: str, question: str, mod, entries, flows: list, clients: list[str]) -> list:
    """Entradas que la consulta nombra por su dominio. Con un cliente nombrado, solo las de ese cliente. Si el nombre COMPLETO de un dominio está en la consulta, ese es el más específico
    («delivery document» no es «delivery document item»); si no, todas las que comparten un término de dominio."""
    named = [c for c in clients if _word(c, f" {q} ")]
    scope = catalog_navigation.flows_for_client(mod, entries, named[0]) if len(named) == 1 else flows
    candidates = [e for e in scope if mod.best_domain_match(question, [e]) is not None]
    words = _name_tokens(q)
    exact = [e for e in candidates if _name_tokens(e.dominio) and _name_tokens(e.dominio) <= words]
    if exact:
        longest = max(len(_name_tokens(e.dominio)) for e in exact)
        return [e for e in exact if len(_name_tokens(e.dominio)) == longest]
    return candidates


def _identities(flows: list, governed: caps.Governed | None) -> list[resolver.Identity]:
    """Las identidades que el resolvedor compara: título del Catálogo (el MISMO resolver de títulos que las filas y el detalle), identificador técnico del modelo terminal y dominio.
    Un flujo sin modelo terminal identificable no tiene título que lo nombre (el marcador neutro no es un nombre)."""
    out = []
    for e in flows:
        try:
            title, technical = presenter.entry_title(governed.statuses[e.entry_id], e) if governed is not None else presenter.flow_heading(e)
        except Exception:  # noqa: BLE001 — un título que no se resuelve deja a la entrada sin clave de título, nunca rompe la consulta
            title, technical = None, None
        out.append(resolver.Identity(e.entry_id, str(e.cliente or ""), "" if not title or title == presenter.NO_TERMINAL_TITLE else title, str(technical or ""), str(e.dominio or "")))
    return out


def _resolve_entry(question: str, flows: list, clients: list[str], governed: caps.Governed | None) -> resolver.Resolution:
    """A qué modelo se refiere la pregunta (paso 4B.0). Las líneas de negocio son ALCANCE (4A), no nombre de modelo, salvo entre «comillas»."""
    labels = [label for lob, label in caps.lob_labels().items() if lob != dcs.UNAVAILABLE]
    return resolver.resolve(question, _identities(flows, governed), tuple(clients), tuple(labels))


def _entry_options(matches, flows: list, governed: caps.Governed | None, pack: str) -> tuple[tuple, ...]:
    """Una opción «Assessment · título · cliente» por modelo coincidente (la URL sale del ``entry_id``)."""
    by_id = {e.entry_id: e for e in flows}
    shown = [by_id[m.entry_id] for m in matches[:MAX_OPTIONS] if m.entry_id in by_id]
    titles = caps.build_items(shown, governed, pack)
    return tuple((f"Assessment · {item.title} · {e.cliente}".strip(" ·"), presenter.assessment_url(e, pack)) for item, e in zip(titles, shown))


def _entry_assessment(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
    q = norm_text(question).strip().lstrip("¿¡")
    if not _is_assessment_request(q):
        return None
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    if not flows:
        return None
    clients = catalog_navigation.clients(mod, entries)
    governed = caps.governed_read(flows)
    res = _resolve_entry(question, flows, clients, governed)           # identidad por TÍTULO del Catálogo (no por dominio); un cliente solo desambigua
    if res.status == resolver.UNIQUE:
        e = next(x for x in flows if x.entry_id == res.matches[0].entry_id)
        title = caps.build_items([e], governed, pack)[0].title
        label = f"{e.cliente} · {e.dominio if title == presenter.NO_TERMINAL_TITLE else title}".strip(" ·")          # sin modelo terminal: el único nombre que existe es el dominio, como antes
        return NavigationResult(f"Acá está el **Assessment** de **{label}**: se abre el informe tal como figura hoy en el Catálogo (o su estado, si todavía no se evaluó).",
                                (("Ver Assessment", presenter.assessment_url(e, pack)),), state=_focus_draft(e.entry_id))
    if res.status in (resolver.AMBIGUOUS, resolver.MULTIPLE):
        more = f" Muestro los primeros {MAX_OPTIONS}: sumá el cliente o el nombre completo para acotar." if len(res.matches) > MAX_OPTIONS else ""
        lead = "Nombraste más de un modelo." if res.status == resolver.MULTIPLE else "Hay más de un modelo que coincide con tu consulta."
        return NavigationResult(f"{lead} ¿De cuál querés ver el Assessment?{more}", _entry_options(res.matches, flows, governed, pack))
    found = _assessment_candidates(q, question, mod, entries, flows, clients)          # sin coincidencia por título: el emparejador por dominio de siempre (equivalencia con los pasos anteriores)
    if not found:
        return None
    ordered = sorted(found, key=lambda e: ((e.cliente or "").lower(), (e.dominio or "").lower(), e.entry_id))
    if len(ordered) == 1:
        e = ordered[0]
        label = f"{e.cliente} · {e.dominio}".strip(" ·")
        return NavigationResult(f"Acá está el **Assessment** de **{label}**: se abre el informe tal como figura hoy en el Catálogo (o su estado, si todavía no se evaluó).",
                                (("Ver Assessment", presenter.assessment_url(e, pack)),))
    shown = ordered[:MAX_OPTIONS]
    more = f" Muestro los primeros {MAX_OPTIONS}: sumá el cliente o el nombre completo para acotar." if len(ordered) > MAX_OPTIONS else ""
    titles = caps.build_items(shown, caps.governed_read(shown), pack)          # el MISMO título que muestran las filas del Catálogo: varios modelos pueden compartir dominio y cliente
    options = tuple((f"Assessment · {item.title} · {e.cliente}".strip(" ·"), presenter.assessment_url(e, pack)) for item, e in zip(titles, shown))
    return NavigationResult(f"Hay más de un modelo que coincide con tu consulta. ¿De cuál querés ver el Assessment?{more}", options)


# ── Consultas INDIVIDUALES sobre un modelo (paso 4B) ────────────────────────────────────────────────────────────────────────────────────────
# «¿Qué casos a revisar tiene Delivery Document?», «¿qué sabés de Lista Precios?», «¿por qué tiene adopción buena?»: se identifica UN modelo con el resolvedor compartido (título del Catálogo; el cliente
# solo desambigua), se recuperan sus datos gobernados (`assistant_entry_facts`) y se redacta con plantillas fijas (`assistant_entry_answers`). Sin LLM. Si no se identifica UN modelo, devuelve None y
# todo lo de 4A queda IDÉNTICO; si se identifica más de uno, se pregunta cuál (nunca se elige por el usuario). La respuesta muestra siempre la identidad (título · cliente · línea de negocio).
def _entry_question(intent: answers.Intent, title: str, client: str) -> str:
    """Pregunta canónica (fijada por el servidor, con el nombre entre comillas) que reenvía al MISMO router la misma consulta sobre UN modelo concreto."""
    name = f"«{title.replace('«', '').replace('»', '')}» de {client}"
    if intent.ficha:
        return f"¿Qué sabés de {name}?"
    if intent.limitations:
        return f"¿Cuáles son las limitaciones de {name}?"
    if intent.strengths and not intent.why:
        return f"¿Cuáles son las fortalezas de {name}?"
    if intent.why:
        return f"¿Cuáles son los fundamentos de {name}?"
    labels = [indicators.BY_ID[i].label for i in intent.attributes] + (["Estado del assessment"] if intent.state else [])
    return f"¿{' y '.join(labels)} de {name}?"


def _entry_choice(intent: answers.Intent, matches, flows: list, governed: caps.Governed | None, pack: str, multiple: bool) -> NavigationResult:
    by_id = {e.entry_id: e for e in flows}
    shown = [by_id[m.entry_id] for m in matches[:MAX_OPTIONS] if m.entry_id in by_id]
    titles = caps.build_items(shown, governed, pack)
    options = tuple(_ask(f"{item.title} · {e.cliente}".strip(" ·"), _entry_question(intent, item.title, str(e.cliente or ""))) for item, e in zip(titles, shown))
    more = f" Muestro los primeros {MAX_OPTIONS}: sumá el cliente o el nombre completo para acotar." if len(matches) > MAX_OPTIONS else ""
    lead = "Nombraste más de un modelo. Consultá uno por vez:" if multiple else "Hay más de un modelo con ese nombre. ¿Cuál querés consultar?"
    return NavigationResult(lead + more, options)


def _entry_query(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
    intent = answers.parse_intent(question)
    if intent is None:
        return None                                                          # compuerta barata: sin una pregunta individual no se paga la carga del Catálogo
    try:
        mod, entries = catalog_reads._catalog_entries()
        flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
        if not flows:
            return None
        governed = caps.governed_read(flows)
        res = _resolve_entry(question, flows, catalog_navigation.clients(mod, entries), governed)
    except Exception:  # noqa: BLE001 — sin poder identificar un modelo no se reclama la pregunta: siguen los destinos de siempre
        log.exception("consulta individual: falló la identificación del modelo")
        return None
    if res.status == resolver.NONE or len(res.clients) > 1:
        return None                                                          # sin UN modelo identificable (o con varios clientes) lo resuelve 4A / el flujo de siempre
    if res.status in (resolver.AMBIGUOUS, resolver.MULTIPLE):
        return _entry_choice(intent, res.matches, flows, governed, pack, res.status == resolver.MULTIPLE)
    try:
        return _entry_answer(intent, next(e for e in flows if e.entry_id == res.matches[0].entry_id), governed, pack)
    except Exception:  # noqa: BLE001 — una consulta individual RECONOCIDA nunca cae en silencio al LLM libre: si falla la lectura, se dice
        log.exception("consulta individual: falló la respuesta")
        return NavigationResult(_FAILED_QUERY, ())


def _entry_answer(intent: answers.Intent, entry, governed: caps.Governed | None, pack: str) -> NavigationResult:
    if governed is None:
        return NavigationResult("No pude leer los datos gobernados en este momento, así que no te muestro valores de ese modelo para no darte un dato incorrecto. Probá de nuevo en unos segundos.", ())
    ids = facts.ATTRIBUTE_ORDER if intent.ficha else tuple(dict.fromkeys(intent.attributes + (("adoption",) if intent.fundamentals else ())))
    ans = facts.answer(entry, governed, pack, ids, with_fundamentals=intent.fundamentals)
    mismatch = _level_mismatch(intent, ans)
    if mismatch is not None:
        return mismatch
    out = answers.render(ans, intent)
    item = dataclasses.replace(ans.entry, badge=out.badge, note=out.note)
    result = caps.ResultSet("entry", total=1, items=(item,), footer=out.footer, entry=ans)
    return NavigationResult(out.message, (ans.links[0],), result, state=_focus_draft(entry.entry_id, ids))


def _level_mismatch(intent: answers.Intent, ans: caps.EntryAnswer) -> NavigationResult | None:
    """«¿Por qué tiene Buena?» fundamenta el nivel VIGENTE: si el nivel pedido no es el actual, se aclara en vez de justificar otro nivel."""
    asked = tuple(level for owner, level in intent.levels if owner == "adoption")
    if not (intent.fundamentals and (asked or intent.nd) and ans.fundamentals is not None and not ans.fundamentals.historical):
        return None
    f = ans.fundamentals
    if asked and f.determined and f.level in asked:
        return None
    if intent.nd and not asked and not f.determined:
        return None
    wanted = indicators.level_text(indicators.BY_ID["adoption"], asked) if asked else "no determinable"
    now = f"**{f.level_label}**" if f.determined else "**no determinable**"
    item = ans.entry
    text = f"**{item.title}** ({item.subtitle}) no tiene hoy adopción **{wanted}**: su Juicio vigente es {now}. Puedo explicarte los fundamentos del Juicio vigente."
    return NavigationResult(text, (ans.links[0],), caps.ResultSet("entry", total=1, items=(item,), entry=ans), state=_focus_draft(item.ref))


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
    offset: int = 0            # desplazamiento de la página (seguimientos: «los siguientes»)
    state: str | None = None   # concepto de estado pedido (assistant_query.STATE_CONCEPTS)
    count: bool = False        # pidió una cantidad: el encabezado contesta el número primero


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


def _catalog_inventory(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
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
    before_state = scope
    if intent.state:
        if governed is None:
            return NavigationResult(_STATES_UNAVAILABLE, tuple(actions[:1]))
        scope = _by_state(scope, intent.state, governed, pack)

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
        return NavigationResult("\n".join(lines), tuple(actions), result, state=qctx.QueryDraft(qctx.QuerySpec("summary")))

    where = _where_text(intent.client, intent.lob, labels, intent.state)
    ordered = caps.order_entries(scope) if not intent.state else caps.order_entries_neutral(scope)           # con estado no hay criterio de orden propio: orden neutral y estable
    spec = qctx.QuerySpec("list", intent.client, intent.lob, limit=intent.limit, offset=intent.offset, state=intent.state, count=intent.count)
    fp = qctx.fingerprint(("list", intent.client, intent.lob) + ((intent.state,) if intent.state else ()),
                          [(caps.CATALOG_ENTRY, e.entry_id, eff[e.entry_id]["group"] if eff is not None else "", e.veredicto, e.cliente, e.dominio)
                           + ((governed.statuses[e.entry_id].state, _state_badge(e, intent.state, governed)) if intent.state else ()) for e in ordered])
    if not ordered:
        if intent.state:                                                                                         # cero explícito, con el denominador y la distribución que separa ambas ausencias
            context = _state_context(before_state, governed)
            return NavigationResult(f"Ninguna entrada {where} (de {len(before_state)} en el alcance). {context}".strip(), tuple(actions), state=qctx.QueryDraft(spec, (), fp))
        return NavigationResult(f"No hay entradas navegables {where}.", tuple(actions), state=qctx.QueryDraft(spec, (), fp))
    shown = ordered[intent.offset:intent.offset + intent.limit]
    if not shown:
        return NavigationResult(f"Ya viste todas las entradas {where} ({len(ordered)}). Pedime «mostrame los anteriores» para volver o una consulta nueva.", tuple(actions), keep_state=True)
    remaining = len(ordered) - intent.offset - len(shown)
    head = f"{'Son' if intent.count else 'Hay'} **{_entries(len(ordered))}** {where}"
    order_note = ("ordenados por cliente y nombre (orden estable, no es un ranking)" if intent.state
                  else "ordenados por mirada Danwor (plena → parcial) y luego por cliente y nombre")
    if intent.offset:
        head += f". Página {intent.offset // intent.limit + 1} de {-(-len(ordered) // intent.limit)}: muestro {len(shown)}, {order_note}:"
    elif remaining > 0:
        head += (f". Muestro una selección de {len(shown)} (el máximo por respuesta), {order_note}:" if intent.capped else f". Muestro {len(shown)}, {order_note}:")        # «selección»/«muestro», no «top»: no hay un ranking real
    else:
        head += f", {order_note}:" if len(ordered) > 1 else ":"
    footer = (f"Hay {remaining} más. Para acotar, sumá un cliente o una línea de negocio, o abrí el Catálogo para explorar el resto. Pedime «mostrame los siguientes» para ver la página siguiente."
              if remaining > 0 else "")
    items = caps.build_items(shown, governed, pack)
    if intent.state:                                                                                             # cada fila muestra el estado REAL de su entrada; el pie, la distribución del alcance
        items = tuple(dataclasses.replace(it, badge=_state_badge(e, intent.state, governed)) for it, e in zip(items, shown))
        footer = " ".join(x for x in (footer, _state_context(before_state, governed)) if x)
    result = caps.ResultSet("list", total=len(ordered), items=items, footer=footer, catalog=caps.CatalogSummary(classified=eff is not None), fingerprint=fp)
    return NavigationResult(head, tuple(actions), result, state=qctx.QueryDraft(spec, tuple(it.key for it in result.items), fp))


# ── Consultas analíticas: ordenar ENTRADAS por UN indicador gobernado explícito ───────────────────────────────────────────────────────────
# «Mejor scoring» no existe como métrica única: cada indicador (registro cerrado de `assistant_indicators`) es una medida distinta y el usuario debe nombrar UNA. Sin indicador → aclaración con las
# métricas reales (cada opción reenvía una pregunta canónica fijada por el servidor al MISMO router, nunca texto del usuario). Con indicador → se resuelve directo con datos reales (solo lectura de los
# contratos gobernados). Sobre dominios, líneas de negocio o clientes NO hay una regla de agregación gobernada: no se corona a ninguno; se ofrece comparar entradas individuales. Toda consulta analítica
# reconocida produce una respuesta gobernada o una aclaración explícita: nunca cae al LLM libre. El orden lo fija el indicador, no el veredicto legado del inventario.
_RK_POS_GEN = re.compile(r"\b(?:mayor(?:es)?|mas alt[oa]s?|maximo)\b")
_RK_NEG_GEN = re.compile(r"\b(?:menor(?:es)?|mas baj[oa]s?|minimo)\b")
_RK_POS_Q = re.compile(r"\bmejor(?:es)?\b")
_RK_NEG_Q = re.compile(r"\bpeor(?:es)?\b")
_RK_MORE, _RK_LESS = re.compile(r"\bmas\b"), re.compile(r"\bmenos\b")          # solo cuentan para indicadores de VOLUMEN
_RK_CUE = re.compile(r"\b(?:ranking|rankear\w*|scoring|puntaje|puntuacion|score|calificacion|evaluacion(?:es)?|riesgos?|ordena\w*|ordenad\w*)\b")
_RK_ENTRY = re.compile(r"\b(?:modelos?|entradas?|flujos?|activos?)\b")
_RK_GROUP = re.compile(r"\b(?:dominios?|lineas? de negocio|clientes?)\b")


@dataclass(frozen=True)
class _RankIntent:
    kind: str                           # rank | clarify_indicator | clarify_direction | group | multi | ambiguous_filter
    indicator: str | None = None
    order: str = indicators.BEST_FIRST
    candidates: tuple[str, ...] = ()    # indicadores nombrados (multi)
    quality_words: bool = False         # usó «mejor/peor» (no aplica a indicadores de volumen)
    lob: str | None = None
    client: str | None = None
    limit: int = MAX_LIST
    capped: bool = False
    offset: int = 0
    levels: tuple[str, ...] = ()        # niveles pedidos del indicador (ids de la fuente)
    level_map: tuple = ()               # multi: ((indicador, niveles),…): cada opción de la aclaración conserva SUS niveles
    state: str | None = None            # concepto de estado pedido (assistant_query.STATE_CONCEPTS)
    count: bool = False                 # pidió una cantidad: el encabezado contesta el número primero


def _ranking_intent(question: str, clients: list[str]) -> _RankIntent | None:
    q = norm_text(question).strip().lstrip("¿¡")
    if is_methodological(indicators.without_indicator_names(q)) or _INV_OTHER.search(q):          # el NOMBRE de un indicador no vuelve metodológica a la pregunta
        return None
    padded = f" {q} "
    named = indicators.indicators_named(q)
    pos_q, neg_q = bool(_RK_POS_Q.search(q)), bool(_RK_NEG_Q.search(q))
    pos_g, neg_g = bool(_RK_POS_GEN.search(q)), bool(_RK_NEG_GEN.search(q))
    cue = pos_q or neg_q or pos_g or neg_g or bool(_RK_CUE.search(q))
    hit_clients = [c for c in clients if _word(c, padded)]
    lobs = _lobs_named(q)
    entry, group = bool(_RK_ENTRY.search(q)), bool(_RK_GROUP.search(q))
    if not (entry or group or hit_clients or lobs):
        return None                                                         # sin materia del Catálogo: no es esta capacidad
    if not (named or cue):
        return None                                                         # inventario puro: lo resuelve catalog_inventory
    count = _INV_COUNT.search(q)
    n = int(next(g for g in count.groups() if g)) if count else 0
    common = dict(lob=lobs[0] if len(lobs) == 1 else None, client=hit_clients[0] if len(hit_clients) == 1 else None, limit=min(n, MAX_LIST) if n else MAX_LIST, capped=n > MAX_LIST,
                  quality_words=pos_q or neg_q)
    if len(hit_clients) > 1 or len(lobs) > 1:
        return _RankIntent("ambiguous_filter", **common)
    if group and not entry:
        return _RankIntent("group", indicator=named[0] if len(named) == 1 else None, candidates=tuple(named), **common)
    if len(named) > 1:
        return _RankIntent("multi", candidates=tuple(named), **common)
    if not named:
        return _RankIntent("clarify_indicator", order=_direction(q, None)[0], **common)
    ind = indicators.BY_ID[named[0]]
    order, clarify = _direction(q, ind)
    if clarify:
        return _RankIntent("clarify_direction", indicator=ind.id, **common)         # «mejor/peor» no aplica a un indicador de volumen
    return _RankIntent("rank", indicator=ind.id, order=order, **common)


def _where_text(client: str | None, lob: str | None, labels: dict[str, str], state: str | None = None) -> str:
    desc = []
    if client:
        desc.append(f"de **{client}**")
    if lob == dcs.UNCLASSIFIED:
        desc.append("**sin clasificar** (sin línea de negocio efectiva)")
    elif lob:
        desc.append(f"en la línea de negocio **{labels[lob]}** (clasificación efectiva)")
    where = " ".join(desc) or "en el Catálogo"
    return f"{where} {_state_clause(state)}" if state else where


def _ask(label: str, question: str) -> tuple:
    return (label, "", f"ask:{question}")


def _indicator_options(intent: _RankIntent, labels: dict[str, str], ids: tuple[str, ...] | None = None) -> tuple[tuple, ...]:
    """Una opción por indicador: reenvía al MISMO router la pregunta canónica (fijada por el servidor) con los filtros ya aplicados. Los indicadores de volumen se omiten si el usuario pidió
    «mejor/peor» (miden cantidad, no calidad)."""
    out = []
    for ind in indicators.INDICATORS:
        if (ids is not None and ind.id not in ids) or (ind.volume and intent.quality_words and ids is None):
            continue
        order = intent.order
        levels = dict(intent.level_map).get(ind.id, ())
        label = ind.label + (f": {indicators.level_text(ind, levels)}" if levels else " (más casos primero)" if ind.volume and order == indicators.BEST_FIRST else " (menos casos primero)" if ind.volume else "")
        state_phrase = query.STATE_BY_ID[intent.state].canonical if intent.state else ""
        out.append(_ask(label, indicators.canonical_question(ind.id, order, client=intent.client, lob_label=labels.get(intent.lob or ""), unclassified=intent.lob == dcs.UNCLASSIFIED,
                                                             levels=levels, state_phrase=state_phrase)))
    return tuple(out)


def _catalog_ranking(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
    mod, entries = catalog_reads._catalog_entries()
    intent = _ranking_intent(question, catalog_navigation.clients(mod, entries) if mod else [])
    if intent is None:
        return None
    try:
        return _ranking_result(intent, pack, stem)
    except Exception:  # noqa: BLE001 — una consulta analítica RECONOCIDA nunca cae en silencio al LLM libre: si falla la ejecución, se dice
        log.exception("consulta analítica del asistente: falló la ejecución")
        return NavigationResult(_FAILED_QUERY, ())


def _ranking_result(intent: _RankIntent, pack: str, stem: str) -> NavigationResult:
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    labels = caps.lob_labels()
    where = _where_text(intent.client, intent.lob, labels, intent.state)
    open_action = _open_catalog_action(pack, stem)
    base = (open_action,) if open_action else ()
    pending = qctx.QueryDraft(qctx.QuerySpec("pending", intent.client, intent.lob, order=intent.order, state=intent.state))       # una aclaración de métrica pendiente: «¿y por confianza?» la completa
    if intent.kind == "ambiguous_filter":
        return NavigationResult("Nombraste más de un cliente o más de una línea de negocio. Para comparar entradas pedime una consulta por vez, con un solo cliente y una sola línea de negocio.", base)
    if intent.kind == "group":
        subject = "dominios, líneas de negocio ni clientes"
        text = (f"No hay una regla de agregación gobernada para ordenar {subject}, así que no puedo decir cuál tiene el «mejor» o el «peor» resultado. "
                "Sí puedo comparar **entradas individuales** según una métrica definida.")
        if intent.indicator:
            options = _indicator_options(intent, labels, (intent.indicator,))
            text += f" Para **{indicators.BY_ID[intent.indicator].label}**:"
        else:
            options = _indicator_options(intent, labels) + (_ask("Ver la distribución por línea de negocio", "¿Qué modelos tenemos?"),)
            text += " ¿Con qué métrica querés compararlas?"
        return NavigationResult(text, options, state=pending)
    if intent.kind == "multi":
        names = [indicators.BY_ID[i].label for i in intent.candidates]
        return NavigationResult(f"Nombraste más de una métrica ({_join(names)}). Son medidas distintas y no las combino en un puntaje: elegí una.", _indicator_options(intent, labels, intent.candidates), state=pending)
    if intent.kind == "clarify_indicator":
        text = ("No existe un «scoring» único: cada métrica mide algo distinto. Para ordenar entradas necesito que elijas una de las métricas disponibles"
                + (f" ({where})" if where != "en el Catálogo" else "") + ".")
        return NavigationResult(text, _indicator_options(intent, labels), state=pending)
    if intent.kind == "clarify_direction":
        ind = indicators.BY_ID[intent.indicator]
        more = indicators.canonical_question(ind.id, indicators.BEST_FIRST, client=intent.client, lob_label=labels.get(intent.lob or ""), unclassified=intent.lob == dcs.UNCLASSIFIED)
        less = indicators.canonical_question(ind.id, indicators.WORST_FIRST, client=intent.client, lob_label=labels.get(intent.lob or ""), unclassified=intent.lob == dcs.UNCLASSIFIED)
        return NavigationResult(f"**{ind.label}** mide cantidad, no calidad: {ind.caveat.split(': ', 1)[-1]} Por eso no hay un «mejor» o un «peor». ¿Querés ver las entradas con más o con menos?",
                                (_ask("Más " + ind.label.lower(), more), _ask("Menos " + ind.label.lower(), less)), state=pending)
    # kind == "rank"
    ind = indicators.BY_ID[intent.indicator]
    if not flows:
        return NavigationResult("El Catálogo todavía no tiene entradas navegables.", ())
    governed = caps.governed_read(flows)
    eff = governed.eff if governed is not None else None
    if governed is None and (intent.lob or intent.state or ind.id != "types"):
        return NavigationResult("No pude leer los datos gobernados en este momento, así que no ordeno por esa métrica para no darte un dato incorrecto. Probá de nuevo en unos segundos o abrí el Catálogo.", base)
    scope = flows
    actions = list(base)
    if intent.client:
        scope = catalog_navigation.flows_for_client(mod, entries, intent.client)
        ref = _reference_flow(mod, entries, intent.client)
        if ref:
            actions.append((f"Informe de Cliente · {intent.client}", _report_url(ref, pack)))
    if intent.lob:
        scope = [e for e in scope if eff[e.entry_id]["group"] == intent.lob]            # SOLO la clasificación efectiva
    if intent.state:
        scope = _by_state(scope, intent.state, governed, pack)
    if not scope:
        return NavigationResult(f"No hay entradas navegables {where}.", tuple(actions))
    levels = intent.levels
    spec = qctx.QuerySpec("rank", intent.client, intent.lob, ind.id, intent.order, intent.limit, intent.offset, levels=levels, state=intent.state, count=intent.count)
    scope_key = (intent.client, intent.lob) + ((levels,) if levels else ()) + ((intent.state,) if intent.state else ())
    rs = indicators.rank_scope(ind, intent.order, scope, governed, pack, intent.limit, intent.offset, scope_key, levels)
    info = rs.ranking
    draft = qctx.QueryDraft(spec, tuple(it.key for it in rs.items), rs.fingerprint)
    if info.evaluated == 0:
        asked = f" (nivel pedido: «{indicators.level_text(ind, levels)}»)" if levels else ""
        return NavigationResult(f"Ninguna de las {_entries(info.universe)} {where} tiene un valor comparable de **{ind.label}**{asked}: {indicators._excluded_text(info.excluded)}. {ind.caveat}", tuple(actions), rs, state=draft)
    if levels:
        return _levels_result(intent, ind, rs, draft, where, actions)
    if not rs.items:
        return NavigationResult(f"Ya viste todas las entradas con valor comparable de **{ind.label}** ({info.evaluated}). Pedime «mostrame los anteriores» para volver o una consulta nueva.", tuple(actions), keep_state=True)
    head = f"Entradas {where} ordenadas por **{ind.label}** ({info.order_text}). {info.evaluated} de {info.universe} tienen un valor comparable."
    if intent.offset:
        head += f" Página {info.page} de {info.pages}: muestro {len(rs.items)}."
    elif len(rs.items) < info.evaluated:
        head += f" Muestro {len(rs.items)}" + (" (el máximo por respuesta)." if intent.capped or intent.limit == MAX_LIST else ".")
    return NavigationResult(head, tuple(actions), rs, state=draft)


# ── Consultas con vocabulario GOBERNADO de nivel, estado y cantidad (paso 4A) ───────────────────────────────────────────────────────────────
# El reconocimiento tipado vive en `assistant_query` (vocabulario DERIVADO de los contratos); la EJECUCIÓN es la de siempre (`_ranking_result` / `_inventory_result` mediante un `QuerySpec`): este
# destino no duplica ninguna consulta ni regla de negocio. Regla: un término gobernado reconocido jamás se ignora en silencio: o se consume o se aclara / se declara la operación no soportada.
# Lo que no trae vocabulario 4A devuelve None y lo resuelven los destinos de siempre (equivalencia con los pasos 0–3).
_DOC_NOUNS = re.compile(r"\b(?:documentacion|documentos?|guias?|recursos?|links?|enlaces?|referencias?|materiales?|material|how-?tos?|templates?|plantillas?|manuales?|tutoriales?|articulos?|glosario|secciones?|capitulos?|bibliografia)\b")


_ASSESSMENTS_WORD = re.compile(r"\b(?:assessments|evaluaciones)\b")          # el PLURAL nombra el conjunto de assessments del Catálogo («el estado de los assessments»); el singular es otra pregunta


@dataclass(frozen=True)
class _Entities:
    clients: tuple[str, ...]
    lobs: tuple[str, ...]
    entry: bool            # nombró entradas («modelos», «entradas», «flujos», «activos»)
    group: bool            # nombró grupos («clientes», «dominios», «líneas de negocio»)
    assessments: bool = False       # nombró los assessments en plural («los assessments»)

    @property
    def scoped(self) -> bool:
        """¿Tiene un alcance resoluble sobre el Catálogo? (un cliente, una línea de negocio, «entradas» o «los assessments»)."""
        return bool(self.clients or self.lobs or self.entry or self.assessments)


def _entities(q: str, clients: list[str]) -> _Entities:
    padded = f" {q} "
    return _Entities(tuple(c for c in clients if _word(c, padded)), tuple(_lobs_named(q)), bool(_RK_ENTRY.search(q)), bool(_RK_GROUP.search(q)), bool(_ASSESSMENTS_WORD.search(q)))


def _query_excluded(q: str, ent: _Entities) -> bool:
    """Preguntas que NO son de este destino aunque compartan vocabulario con el Catálogo: metodológicas, de otros inventarios y documentales (sin alcance sobre entradas)."""
    if is_methodological(indicators.without_indicator_names(q)) or _INV_OTHER.search(q):
        return True
    return bool(_DOC_NOUNS.search(q)) and not ent.scoped


def _level_options(ind, client: str | None, lob: str | None, labels: dict[str, str]) -> tuple[tuple, ...]:
    """Una opción por nivel del indicador: reenvía al MISMO router la pregunta canónica (fijada por el servidor) con ese nivel y los filtros ya aplicados."""
    return tuple(_ask(f"{ind.label}: {label}", indicators.canonical_question(ind.id, indicators.BEST_FIRST, client=client, lob_label=labels.get(lob or ""),
                                                                              unclassified=lob == dcs.UNCLASSIFIED, levels=(lv,)))
                 for lv, label in ind.level_labels)


def _levels_clarification(ind, frame: query.QueryFrame, client: str | None, lob: str | None, labels: dict[str, str], base: tuple) -> NavigationResult:
    """Un nivel que el indicador no tiene (o un indicador sin niveles): jamás se filtra por «lo más parecido» ni se responde con un ranking que lo ignore."""
    if query.NEGATED_LEVEL in frame.ops:
        return NavigationResult(f"Todavía no filtro por la negación de un nivel («sin…», «no…»). Decime qué nivel de **{ind.label}** querés ver:", _level_options(ind, client, lob, labels) or base)
    if ind.levels:
        names = _join([label for _lv, label in ind.level_labels])
        return NavigationResult(f"No reconozco ese nivel para **{ind.label}**. Sus niveles son: {names}. ¿Cuál querés ver?", _level_options(ind, client, lob, labels))
    kind = {"ratio": "un porcentaje continuo", "score": "un puntaje continuo (0–100)", "count": "un conteo"}.get(ind.kind, "una medida continua")
    options = _indicator_options(_RankIntent("clarify_indicator", client=client, lob=lob), labels, (ind.id,))
    return NavigationResult(f"**{ind.label}** es {kind}: no tiene niveles gobernados (como «buena» o «alta»), así que no puedo filtrar por un nivel. Sí puedo ordenar las entradas por esa métrica.", options or base)


def _catalog_query(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
    frame = query.parse(question)
    if not frame.vocabulary:
        return None                                                          # compuerta barata: sin vocabulario 4A no se paga ni la carga del Catálogo
    q = norm_text(question).strip().lstrip("¿¡")
    mod, entries = catalog_reads._catalog_entries()
    ent = _entities(q, catalog_navigation.clients(mod, entries) if mod else [])
    if _query_excluded(q, ent):
        return None
    try:
        return _query_resolve(q, frame, ent, pack, stem)
    except Exception:  # noqa: BLE001 — una consulta con vocabulario gobernado RECONOCIDO nunca cae en silencio al LLM libre: si falla la ejecución, se dice
        log.exception("consulta con vocabulario gobernado: falló la ejecución")
        return NavigationResult(_FAILED_QUERY, ())


def _indicator_query(q: str, frame: query.QueryFrame, client: str | None, lob: str | None, common: dict, state: str | None, labels: dict[str, str], base: tuple,
                     pack: str, stem: str) -> NavigationResult:
    """Consulta con UN indicador (o varios, que se aclaran): niveles y estado se consumen o se aclaran; la ejecución es `_ranking_result`."""
    if len(frame.indicators) > 1:                                            # dos métricas no se combinan: se elige UNA; cada opción conserva SUS niveles y el estado pedido
        level_map = tuple((i, frame.levels_of(i)) for i in frame.indicators if frame.levels_of(i))
        return _ranking_result(_RankIntent("multi", candidates=frame.indicators, level_map=level_map, state=state, client=client, lob=lob, **common), pack, stem)
    ind = indicators.BY_ID[frame.indicators[0]]
    foreign = any(owner != ind.id for owner, _lv in frame.levels)
    if query.NEGATED_LEVEL in frame.ops or (frame.levels and (not ind.levels or foreign)):
        return _levels_clarification(ind, frame, client, lob, labels, base)
    levels = tuple(lv for lv in ind.levels if lv in set(frame.levels_of(ind.id)))
    return _ranking_result(_RankIntent("rank", ind.id, _direction(q, ind)[0], levels=levels, state=state, client=client, lob=lob, **common), pack, stem)


def _query_resolve(q: str, frame: query.QueryFrame, ent: _Entities, pack: str, stem: str) -> NavigationResult | None:
    labels = caps.lob_labels()
    open_action = _open_catalog_action(pack, stem)
    base = (open_action,) if open_action else ()
    inds = frame.indicators
    governed = bool(inds or frame.states or frame.levels or frame.dimension_state)
    strong = bool(ent.entry or ent.clients or ent.assessments)                    # alcance fuerte: sin vocabulario gobernado, una operación solo se reclama si habla de entradas o de un cliente
    # 1. operación NO soportada → declaración explícita (nunca un ranking que la ignore)
    ops = _declared_ops(frame, ent)
    group_rank = bool(ent.group and (frame.states or frame.levels) and _GROUP_SUPERLATIVE.search(q))       # ranking de GRUPOS por estado o nivel: sin regla de agregación gobernada
    if group_rank:
        return _declaration("group_rank", frame, ent, labels, base)
    if ops and (governed or strong):
        return _declaration(ops[0], frame, ent, labels, base)
    # 2. conteo agrupado por cliente o por línea de negocio (cardinalidades)
    if frame.group_by:
        if inds or frame.levels:
            return _declaration(query.AGGREGATE, frame, ent, labels, base)
        if not frame.count:
            return _group_by_clarification(frame.group_by)
        if len(ent.lobs) > 1 or len(frame.states) > 1:
            return _ranking_result(_RankIntent("ambiguous_filter"), pack, stem) if len(ent.lobs) > 1 else _states_clarification(frame, None, None, labels)
        state = frame.states[0] if frame.states else None
        return _groups_result(frame.group_by, ent.lobs[0] if ent.lobs else None, state, pack, stem)
    # 3. métrica o estado nombrados sin alcance resoluble → declaración «necesito un alcance»
    if (inds or frame.states) and not ent.scoped and not ent.group and _data_cue(q):
        return _need_scope(frame, labels, base)
    wants_levels = bool(frame.levels and inds)
    dimension = frame.dimension_state and not frame.states
    if dimension and frame.superlative and not inds:
        return None                                                          # «mejor/peor estado»: «estado» no es una métrica ordenable; lo sigue aclarando el ranking (no se contesta con una distribución que ignore «mejor»)
    threshold = bool(frame.threshold_word and inds)
    if not (wants_levels or frame.states or dimension or threshold):
        return None                                                          # sin calificador gobernado por consumir: lo resuelven los destinos de siempre (equivalencia con los pasos 0–3)
    if not ent.scoped or (ent.group and not ent.entry):
        return None
    if len(ent.clients) > 1 or len(ent.lobs) > 1:
        return _ranking_result(_RankIntent("ambiguous_filter"), pack, stem)
    client = ent.clients[0] if ent.clients else None
    lob = ent.lobs[0] if ent.lobs else None
    if threshold and not wants_levels and not frame.states:
        ordinal_or_not = next((indicators.BY_ID[i] for i in inds if not indicators.BY_ID[i].levels), indicators.BY_ID[inds[0]])
        return _levels_clarification(ordinal_or_not, frame, client, lob, labels, base)
    m = _INV_COUNT.search(q)
    n = int(next(g for g in m.groups() if g)) if m else 0
    limit, capped = (min(n, MAX_LIST) if n else MAX_LIST), n > MAX_LIST
    common = dict(limit=limit, capped=capped, count=frame.count)
    if dimension:
        if inds:
            return _dimension_clarification(frame, client, lob, labels)
        return _states_result(client, lob, pack, stem)
    if not frame.states:
        return _indicator_query(q, frame, client, lob, common, None, labels, base, pack, stem)
    if len(frame.states) > 1:
        return _states_clarification(frame, client, lob, labels)
    state = frame.states[0]
    if query.STATE_BY_ID[state].judgment:
        if frame.levels or any(i != "adoption" for i in inds):
            return _judgment_clarification(frame, client, lob, labels)
        return _inventory_result(_InventoryIntent("list", limit, capped, lob, client, state=state, count=frame.count), pack, stem)
    if not inds:
        return _inventory_result(_InventoryIntent("list", limit, capped, lob, client, state=state, count=frame.count), pack, stem)
    return _indicator_query(q, frame, client, lob, common, state, labels, base, pack, stem)


def _state_clause(state_id: str) -> str:
    """El filtro de estado tal como se repite en el encabezado (eco de la interpretación)."""
    c = query.STATE_BY_ID[state_id]
    return "con adopción metodológica **no determinable**" if c.judgment else f"con estado **{c.label}**"


def _by_state(scope: list, state_id: str, governed: caps.Governed, pack: str) -> list:
    """Las entradas del alcance cuyo estado coincide con el concepto pedido, según la lectura gobernada VIGENTE. «No determinable» es la medición de adopción del registro (misma regla): no se reimplementa."""
    c = query.STATE_BY_ID[state_id]
    if c.judgment:
        adoption = indicators.BY_ID["adoption"]
        return [e for e in scope if adoption.measure(e, governed, pack).state == indicators.NOT_DETERMINABLE]
    return [e for e in scope if governed.statuses[e.entry_id].state in c.states]


def _state_parts(lines: tuple, breakdown: tuple) -> str:
    """La distribución por estado en texto, separando AUSENCIA DE EVALUACIÓN («sin assessment») de AUSENCIA DE RESULTADO UTILIZABLE (no aplica / no disponible / no compatible)."""
    # Cláusulas separadas por «;» y NUNCA un punto detrás de un número: el render del chat (`render_llm_html`) interpreta «13. » como el inicio de una lista numerada y se come el número.
    parts = [" · ".join(f"{label}: {n}" for _id, label, n in lines)]
    if breakdown:
        parts.append("«Sin resultado utilizable» abre en " + " · ".join(f"{label}: {n}" for label, n in breakdown))
    if any(cid in (query.NOT_EVALUATED_ID, query.NO_USABLE_RESULT_ID) for cid, _l, _n in lines):
        parts.append("«Sin assessment» (nunca se evaluó) y «sin resultado utilizable» (no aplica, no disponible o no compatible) son conceptos distintos")
    return "; ".join(parts) + ("." if not parts[-1][-1:].isdigit() else "")


def _state_context(scope: list, governed: caps.Governed) -> str:
    lines, breakdown = query.distribution(governed.statuses[e.entry_id].state for e in scope)
    return f"Estado de las {_entries(len(scope))} del alcance: " + _state_parts(lines, breakdown) if lines else ""


def _state_badge(entry, state_id: str, governed: caps.Governed) -> str:
    """Rótulo de estado de la fila de un listado por estado: el estado REAL de esa entrada (un grupo abarca varios)."""
    if query.STATE_BY_ID[state_id].judgment:
        return query.STATE_BY_ID[state_id].label
    return query.concept_for_state(governed.statuses[entry.entry_id].state).label


_STATES_UNAVAILABLE = ("No pude leer los estados de assessment en este momento, así que no filtro ni cuento por estado para no darte un dato incorrecto. "
                       "Probá de nuevo en unos segundos o abrí el Catálogo.")


def _states_result(client: str | None, lob: str | None, pack: str, stem: str) -> NavigationResult:
    """Distribución por estado del assessment de un alcance (cliente / línea de negocio): las líneas PARTEN el universo (su suma es el total). No hay filas ni posiciones."""
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    if not flows:
        return NavigationResult("El Catálogo todavía no tiene entradas navegables.", ())
    open_action = _open_catalog_action(pack, stem)
    actions: list[tuple] = [open_action] if open_action else []
    governed = caps.governed_read(flows)
    if governed is None:
        return NavigationResult(_STATES_UNAVAILABLE, tuple(actions))
    scope = flows
    if client:
        scope = catalog_navigation.flows_for_client(mod, entries, client)
        ref = _reference_flow(mod, entries, client)
        if ref:
            actions.append((f"Informe de Cliente · {client}", _report_url(ref, pack)))
    if lob:
        scope = [e for e in scope if governed.eff[e.entry_id]["group"] == lob]
    where = _where_text(client, lob, caps.lob_labels())
    spec = qctx.QuerySpec("states", client, lob)
    if not scope:
        return NavigationResult(f"No hay entradas navegables {where}.", tuple(actions), state=qctx.QueryDraft(spec))
    lines, breakdown = query.distribution(governed.statuses[e.entry_id].state for e in scope)
    fp = qctx.fingerprint(("states", client, lob), [(caps.CATALOG_ENTRY, e.entry_id, governed.statuses[e.entry_id].state) for e in caps.order_entries_neutral(scope)])
    result = caps.ResultSet("summary", total=len(scope), catalog=caps.CatalogSummary(aggregates=lines, classified=True), fingerprint=fp)
    text = f"**Estado de los assessments** {where} ({_entries(len(scope))}): " + _state_parts(lines, breakdown)
    return NavigationResult(text, tuple(actions), result, state=qctx.QueryDraft(spec, (), fp))


def _canonical_where(client: str | None, lob: str | None, labels: dict[str, str]) -> str:
    return (f" de {client}" if client else "") + (" sin clasificar" if lob == dcs.UNCLASSIFIED else (f" en {labels.get(lob or '')}" if lob else ""))


def _state_question(state_id: str, client: str | None, lob: str | None, labels: dict[str, str]) -> str:
    """Pregunta canónica (fijada por el servidor) de un listado por estado; reconocible de ida y vuelta."""
    return f"¿Qué entradas{_canonical_where(client, lob, labels)} {query.STATE_BY_ID[state_id].canonical}?"


def _states_clarification(frame: query.QueryFrame, client: str | None, lob: str | None, labels: dict[str, str]) -> NavigationResult:
    names = _join([query.STATE_BY_ID[s].label for s in frame.states])
    options = tuple(_ask(query.STATE_BY_ID[s].label, _state_question(s, client, lob, labels)) for s in frame.states)
    return NavigationResult(f"Nombraste más de un estado ({names}). Son conceptos distintos y no los uno: pedime uno por vez.", options)


def _judgment_clarification(frame: query.QueryFrame, client: str | None, lob: str | None, labels: dict[str, str]) -> NavigationResult:
    """«No determinable» es la ausencia de un nivel del Juicio de adopción: no se combina con un nivel ni con otro indicador."""
    adoption = indicators.BY_ID["adoption"]
    options = (_ask("Adopción no determinable", _state_question(query.NOT_DETERMINABLE_ID, client, lob, labels)),) + _level_options(adoption, client, lob, labels)
    return NavigationResult(f"«No determinable» describe un Juicio sin nivel y es una condición de **{adoption.label}**: no la combino con un nivel ni con otra métrica. Pedime una cosa por vez:", options)


def _dimension_clarification(frame: query.QueryFrame, client: str | None, lob: str | None, labels: dict[str, str]) -> NavigationResult:
    """«Estado» junto a una métrica puede ser el estado del assessment o el valor de esa métrica: no se elige por el usuario."""
    where = _canonical_where(client, lob, labels)
    options = (_ask("Estado de los assessments", f"¿Cuál es el estado de los assessments{where}?"),) + _indicator_options(_RankIntent("clarify_indicator", client=client, lob=lob), labels, frame.indicators)
    return NavigationResult("«Estado» puede ser el estado del assessment (vigente, desactualizado…) o el valor de una métrica. ¿Cuál querés ver?", options)


_GROUP_SUPERLATIVE = re.compile(r"\b(?:clientes?|dominios?|lineas? de negocio)\b.{0,40}\b(?:mas|menos|mayor(?:es)?|menor(?:es)?|mejor(?:es)?|peor(?:es)?)\b")      # «el cliente con más…», «la línea de negocio con menor…»


def _data_cue(q: str) -> bool:
    """¿Es una pregunta de DATO (posesión, existencia, cantidad o pedido de ver)? Reutiliza las señales del inventario: sin listas nuevas."""
    return bool(_INV_CUE.search(q) or _NAV_VERB.search(q) or _INV_LIST_VERB.search(q) or _INV_TIENE.search(q))


def _declared_ops(frame: query.QueryFrame, ent: _Entities) -> list[str]:
    """Operaciones NO soportadas que la consulta pide (taxonomía cerrada de `assistant_query` + las que dependen del indicador: «total»/«cuántos» sobre una métrica de VOLUMEN suman casos)."""
    inds = [indicators.BY_ID[i] for i in frame.indicators]
    ops = [o for o in frame.ops if o != query.NEGATED_LEVEL]
    volume = any(i.volume for i in inds)
    if volume and (frame.total_word or (frame.count and not frame.levels and not frame.states and not frame.group_by)) and query.AGGREGATE not in ops:
        ops.append(query.AGGREGATE)                                                    # «¿cuántos casos a revisar tiene X?» / «…en total»: sumar casos es una agregación sin regla gobernada
    return ops


def _declaration(op: str, frame: query.QueryFrame, ent: _Entities, labels: dict[str, str], base: tuple) -> NavigationResult:
    """Declaración EXPLÍCITA de una operación no soportada (una plantilla por clase; agregar una clase = agregar una fila). Nunca repite el texto del usuario; ofrece lo que sí se puede hacer."""
    client = ent.clients[0] if len(ent.clients) == 1 else None
    lob = ent.lobs[0] if len(ent.lobs) == 1 else None
    ids = tuple(frame.indicators) or None
    metric_options = _indicator_options(_RankIntent("clarify_indicator", client=client, lob=lob), labels, ids)
    if op == query.COMPARE:
        who = tuple(_ask(c, f"¿Qué tiene {c}?") for c in ent.clients) + tuple(_ask(labels[o], f"Mostrame los modelos en {labels[o]}") for o in ent.lobs if o in labels and o != dcs.UNCLASSIFIED)
        return NavigationResult("No comparo grupos entre sí: no hay una regla de agregación gobernada para decir cuál es mejor. Puedo mostrarte cada uno por separado:", who or base)
    if op == query.THRESHOLD:
        levelless = next((indicators.BY_ID[i] for i in frame.indicators if not indicators.BY_ID[i].levels), None)
        ind = levelless or (indicators.BY_ID[frame.indicators[0]] if frame.indicators else None)
        if ind is not None:
            return _levels_clarification(ind, frame, client, lob, labels, base)
    if op == "group_rank":
        return NavigationResult("No hay una regla de agregación gobernada para ordenar clientes, líneas de negocio ni dominios, así que no puedo decir cuál tiene «más» o «menos». Sí puedo contar entradas por cliente o por línea de negocio.",
                                (_ask("Cuántas entradas por cliente", "¿Cuántos modelos hay por cliente?"), _ask("Cuántas entradas por línea de negocio", "¿Cuántos modelos hay por línea de negocio?")))
    return NavigationResult("No calculo promedios, sumas ni porcentajes: no hay una regla de agregación gobernada (cada métrica se evalúa por entrada, no por grupo). Sí puedo ordenar o contar entradas.",
                            metric_options + (_ask("Ver el resumen del Catálogo", "¿Qué modelos tenemos?"),))


def _need_scope(frame: query.QueryFrame, labels: dict[str, str], base: tuple) -> NavigationResult:
    """Una métrica o un estado nombrados SIN alcance resoluble (cliente, línea de negocio o entradas): no se responde con un recurso documental ni con el LLM libre; se declara qué falta y qué sí se puede."""
    terms = _join([f"{indicators.BY_ID[i].label}" for i in frame.indicators] + [query.STATE_BY_ID[s].label for s in frame.states])
    options = _indicator_options(_RankIntent("clarify_indicator"), labels, tuple(frame.indicators)) if frame.indicators else ()
    options += tuple(_ask(f"Ver las entradas: {query.STATE_BY_ID[s].label}", _state_question(s, None, None, labels)) for s in frame.states)
    return NavigationResult(f"Entendí que preguntás por **{terms}**, pero no pude ubicar un cliente, una línea de negocio ni un conjunto de entradas. Por ahora el chat ordena y cuenta **entradas** por cliente, "
                            "línea de negocio, nivel o estado, y no lee el valor de una entrada puntual (eso queda para el detalle del modelo en el Catálogo).", options + base)


def _groups_result(group_by: str, lob: str | None, state: str | None, pack: str, stem: str, client: str | None = None) -> NavigationResult:
    """Conteo AGRUPADO de entradas por cliente o por línea de negocio efectiva (cardinalidades: no se ordena por calidad ni se corona a ningún grupo). Las líneas PARTEN el alcance."""
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    if not flows:
        return NavigationResult("El Catálogo todavía no tiene entradas navegables.", ())
    open_action = _open_catalog_action(pack, stem)
    actions = (open_action,) if open_action else ()
    labels = caps.lob_labels()
    governed = caps.governed_read(flows)
    if governed is None:
        return NavigationResult("No pude leer la clasificación ni los estados en este momento, así que no cuento por grupo para no darte un dato incorrecto. Probá de nuevo en unos segundos o abrí el Catálogo.", actions)
    scope = flows
    if client:
        scope = catalog_navigation.flows_for_client(mod, entries, client)
    if lob:
        scope = [e for e in scope if governed.eff[e.entry_id]["group"] == lob]
    if state:
        scope = _by_state(scope, state, governed, pack)
    where = _where_text(client, lob, labels, state)
    spec = qctx.QuerySpec("groups", client, lob, state=state, group_by=group_by, count=True)
    if not scope:
        return NavigationResult(f"Ninguna entrada {where} (de {len(flows)} entradas navegables).", actions, state=qctx.QueryDraft(spec))
    if group_by == "client":
        counts: dict[str, int] = {}
        for e in scope:
            counts[e.cliente] = counts.get(e.cliente, 0) + 1
        aggregates = tuple((c, c, n) for c, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())))
        title, what = "por cliente", "Entradas navegables por cliente"
    else:
        aggregates = caps.distribution(scope, governed.eff, labels)
        title, what = "por línea de negocio", "Entradas navegables por línea de negocio (clasificación efectiva)"
    fp = qctx.fingerprint(("groups", group_by, client, lob, state), [(caps.CATALOG_ENTRY, e.entry_id, e.cliente, governed.eff[e.entry_id]["group"]) for e in caps.order_entries_neutral(scope)])
    result = caps.ResultSet("summary", total=len(scope), catalog=caps.CatalogSummary(aggregates=aggregates, classified=True), fingerprint=fp)
    text = f"**{what}** {where} ({_entries(len(scope))}): {_distribution_text(aggregates)}"
    return NavigationResult(text, actions, result, state=qctx.QueryDraft(spec, (), fp))


def _group_by_clarification(group_by: str) -> NavigationResult:
    label = "por cliente" if group_by == "client" else "por línea de negocio"
    return NavigationResult(f"Para ver cuántas entradas hay {label} pedímelo como una cantidad: no agrupo listados ni métricas.",
                            (_ask(f"Cuántas entradas {label}", f"¿Cuántos modelos hay {label}?"),))


def _levels_result(intent: "_RankIntent", ind, rs: caps.ResultSet, draft: qctx.QueryDraft, where: str, actions: list) -> NavigationResult:
    """Respuesta de una consulta filtrada por NIVEL: el encabezado repite el filtro efectivo (indicador, niveles, alcance) y el denominador, y cuenta lo que no se compara. Cero es «Ninguna»."""
    info = rs.ranking
    what = f"**{ind.label}** «{info.level_text}»"
    denominator = f"de {info.universe}; {info.evaluated} con valor comparable"
    if info.matched == 0:
        excluded = f"No se comparan: {indicators._excluded_text(info.excluded)}. " if info.excluded else ""
        return NavigationResult(f"Ninguna entrada {where} tiene {what} ({denominator}). {excluded}{ind.caveat}", tuple(actions), rs, state=draft)
    if not rs.items:
        return NavigationResult(f"Ya viste todas las entradas {where} con {what} ({info.matched}). Pedime «mostrame los anteriores» para volver o una consulta nueva.", tuple(actions), keep_state=True)
    head = f"{'Son' if intent.count else 'Hay'} **{_entries(info.matched)}** {where} con {what} ({denominator})."
    if intent.offset:
        head += f" Página {info.page} de {info.pages}: muestro {len(rs.items)}."
    elif len(rs.items) < info.matched:
        head += f" Muestro {len(rs.items)}" + (" (el máximo por respuesta)." if intent.capped or intent.limit == MAX_LIST else ".")
    return NavigationResult(head, tuple(actions), rs, state=draft)


# ── Seguimientos conversacionales sobre la última consulta estructurada ───────────────────────────────────────────────────────────────────
# «¿Y los de Finanzas?», «¿y por confianza?», «los siguientes», «abrí el segundo», «empezar de nuevo». La gramática es ACOTADA y se DERIVA de los contratos que ya existen (clientes del Catálogo,
# taxonomía de líneas de negocio, alias del registro de indicadores): no hay un intent por frase. Un mensaje es un seguimiento solo si, quitadas las entidades conocidas, los alias de indicador, las palabras
# de dirección y las muletillas, NO queda nada: una pregunta con contenido propio («¿qué tiene Pampa?», «modelos de Ventas») es una consulta NUEVA y reemplaza el contexto. El contexto (assistant_context)
# guarda solo la consulta y las identidades mostradas: todo se RECALCULA desde las fuentes gobernadas vigentes antes de paginar, filtrar o abrir una referencia.
_FU_INTERROGATIVES = {"que", "como", "cual", "cuales", "quien", "quienes", "donde", "cuando", "cuanto", "cuantos", "cuantas", "hay", "tenemos", "existe", "existen", "lista", "listame"}
_FU_LEAD = ("y", "solo", "solamente", "unicamente", "pero", "tambien", "por", "segun")
_FU_FILLERS = {"y", "e", "los", "las", "el", "la", "lo", "de", "del", "para", "en", "por", "segun", "solo", "solamente", "unicamente", "nada", "pero", "tambien", "ahora", "quiero", "ver", "mostrame", "muestrame",
               "mostra", "dame", "pasame", "ordena", "ordenalos", "ordenalas", "ordenar", "ordenado", "ordenados", "ordenadas", "ordename", "ordenamelos", "linea", "lineas", "negocio", "cliente", "clientes", "nivel",
               "esos", "esas"}
_FU_NOUNS = {"modelos", "modelo", "entradas", "entrada", "resultados", "resultado", "con"}          # solo cuentan como muletilla si el mensaje arranca como continuación («y los modelos de Ventas…»)
_FU_PAGE_LEAD = r"(?:(?:y|mostrame|muestrame|mostra|ver|dame|pasame|quiero ver|los|las|unos|el|la)\s+)*"
_FU_NEXT = re.compile(rf"^{_FU_PAGE_LEAD}(?:pagina\s+)?(?:siguientes?|proxim[oa]s?|mas)(?:\s+(?:resultados|entradas|modelos|paginas?))?$")
_FU_PREV = re.compile(rf"^{_FU_PAGE_LEAD}(?:pagina\s+)?(?:anteriores|anterior|previos|previas)(?:\s+(?:resultados|entradas|modelos|paginas?))?$")
_FU_COUNT = re.compile(r"^(?:y\s+)?(?:cuantos|cuantas)(?:\s+(?:son|hay|resultados|entradas|modelos))?$")                  # «¿cuántos son?»: la cantidad de la consulta vigente (D-4)
_FU_RESET = re.compile(r"^(?:(?:y|quiero|podemos|vamos a)\s+)?(?:empezar|empeza|empecemos|comenzar|comenza|arrancar|arranca|reiniciar|reinicia)\s+(?:de\s+)?(?:nuevo|cero)$"
                       r"|^(?:una\s+)?nueva\s+consulta$|^(?:olvida|olvidate de|borra|borrar)\s+(?:lo anterior|el contexto|todo)$|^desde cero$")
_ORDINALS = {"primero": 1, "primer": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercero": 3, "tercer": 3, "tercera": 3, "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "sexto": 6, "sexta": 6,
             "septimo": 7, "septima": 7, "octavo": 8, "octava": 8, "noveno": 9, "novena": 9, "decimo": 10, "decima": 10,
             "undecimo": 11, "undecima": 11, "duodecimo": 12, "duodecima": 12, "decimotercero": 13, "decimocuarto": 14, "decimoquinto": 15, "decimosexto": 16, "decimoseptimo": 17,
             "decimoctavo": 18, "decimonoveno": 19, "vigesimo": 20, "vigesima": 20}
_ORDINAL_TEXT = {1: "primero", 2: "segundo", 3: "tercero", 4: "cuarto", 5: "quinto", 6: "sexto", 7: "séptimo", 8: "octavo", 9: "noveno", 10: "décimo", 11: "undécimo", 12: "duodécimo", 13: "decimotercero", 14: "decimocuarto", 15: "decimoquinto", 16: "decimosexto", 17: "decimoséptimo", 18: "decimoctavo", 19: "decimonoveno", 20: "vigésimo"}
_FU_REF = re.compile(r"^(?:(?:abri|abrime|abre|mostrame|muestrame|mostra|ver|veo|dame|pasame|quiero ver|llevame a|ir a|entra a|y)\s+)*(?:(?:el|la|al)\s+)?(?:numero\s+)?"
                     rf"(?P<w>{'|'.join(sorted(_ORDINALS, key=len, reverse=True))}|ultim[oa]|\d{{1,2}}(?:o|a|º|°)?|ese|esa|este|esta)(?:\s+(?:modelo|entrada|resultado))?(?:\s+de la lista)?$")
_FAILED_QUERY = "No pude completar la consulta en este momento, así que no te muestro un dato dudoso. Probá de nuevo en unos segundos o hacé una consulta nueva."
_FU_HELP = "Pedime, por ejemplo, «¿Qué modelos tenemos?» o «¿Qué entradas tienen mejor adopción metodológica?»."


@dataclass(frozen=True)
class _Follow:
    op: str                                   # reset | next | prev | ref | change
    ordinal: int | None = None                # ref: posición 1-based en la lista VISIBLE; -1 = último; 0 = «ese/esta» (solo si es inequívoco)
    clients: tuple[str, ...] = ()
    lobs: tuple[str, ...] = ()
    inds: tuple[str, ...] = ()
    order: str = indicators.BEST_FIRST
    clarify_direction: bool = False
    levels: tuple[tuple[str, str], ...] = ()      # (indicador dueño, nivel) nombrados junto a su indicador
    states: tuple[str, ...] = ()                  # conceptos de estado nombrados
    count: bool = False                           # change: pidió una cantidad · op «count»: «¿cuántos son?»


def _direction(q: str, ind) -> tuple[str, bool]:
    """(orden, aclarar_dirección) de una consulta normalizada. «mejor/peor» no aplica a un indicador de VOLUMEN (más/menos): en ese caso se pide aclarar."""
    pos_q, neg_q = bool(_RK_POS_Q.search(q)), bool(_RK_NEG_Q.search(q))
    pos_g, neg_g = bool(_RK_POS_GEN.search(q)), bool(_RK_NEG_GEN.search(q))
    if ind is None:
        return (indicators.WORST_FIRST if (neg_q or neg_g) and not (pos_q or pos_g) else indicators.BEST_FIRST), False
    more, less = bool(_RK_MORE.search(q)), bool(_RK_LESS.search(q))
    pos = pos_g or (more if ind.volume else pos_q)
    neg = neg_g or (less if ind.volume else neg_q)
    if ind.volume and (pos_q or neg_q) and not (pos_g or neg_g or more or less):
        return indicators.BEST_FIRST, True
    if not ind.volume:
        pos, neg = pos or pos_q, neg or neg_q
    return (indicators.WORST_FIRST if neg and not pos else indicators.BEST_FIRST), False


def _followup_intent(question: str, clients: list[str], live: bool) -> _Follow | None:
    t = re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", norm_text(question))).strip()
    words = t.split()
    if t and _FU_COUNT.match(t):
        return _Follow("count")
    if not t or len(words) > 9 or words[0] in _FU_INTERROGATIVES:
        return None                                                          # un seguimiento es corto y no es una pregunta nueva
    if _FU_RESET.match(t):
        return _Follow("reset")
    if _FU_NEXT.match(t):
        return _Follow("next")
    if _FU_PREV.match(t):
        return _Follow("prev")
    m = _FU_REF.match(t)
    if m:
        w = m.group("w")
        ordinal = (-1 if w.startswith("ultim") else 0 if w in ("ese", "esa", "este", "esta") else _ORDINALS.get(w) or int(re.match(r"\d+", w).group()))
        return _Follow("ref", ordinal=ordinal)
    padded = f" {t} "
    hit_clients = [c for c in clients if _word(c, padded)]
    lobs = _lobs_named(t)
    inds = indicators.indicators_named(t)
    frame = query.parse(t)
    if frame.ops or frame.group_by or (frame.levels and not frame.indicators):
        return None                                                          # operación no soportada, agrupación o nivel SIN su indicador: no es un seguimiento de filtro (el nivel solo vale junto a su indicador)
    if not (hit_clients or lobs or inds or frame.states):
        return None
    rest = f" {frame.masked} "                                               # el texto sin estados, niveles, cantidad ni nombres de indicador (vocabulario derivado de los contratos)
    for c in hit_clients:
        rest = re.sub(rf"(?<![a-z0-9]){re.escape(norm_text(c))}(?![a-z0-9])", " ", rest)
    for o in dcs.lob_options():
        if o["id"] != dcs.UNCLASSIFIED:
            rest = re.sub(rf"(?<![a-z0-9]){re.escape(norm_text(o['label']))}(?![a-z0-9])", " ", rest)
    rest = _INV_UNCLASSIFIED.sub(" ", rest)
    rest = re.sub(r"\bnada mas\b", " ", rest)
    rest = indicators.without_indicator_names(rest.strip())
    for rx in (_RK_POS_Q, _RK_NEG_Q, _RK_POS_GEN, _RK_NEG_GEN, _RK_MORE, _RK_LESS):
        rest = rx.sub(" ", rest)
    lead = words[0] in _FU_LEAD or t.startswith("nada mas") or words[0].startswith("ordena")
    allowed = _FU_FILLERS | (_FU_NOUNS if lead else set())
    if any(tok not in allowed for tok in rest.split()):
        return None                                                          # tiene contenido propio: es una consulta nueva
    only_unclassified = lobs == [dcs.UNCLASSIFIED] and not hit_clients and not inds
    if not (lead or live or only_unclassified):
        return None                                                          # sin contexto y sin marca de continuación, una entidad suelta no se interpreta como seguimiento
    ind = indicators.BY_ID[inds[0]] if len(inds) == 1 else None
    order, clarify = _direction(t, ind)
    return _Follow("change", clients=tuple(hit_clients), lobs=tuple(lobs), inds=tuple(inds), order=order, clarify_direction=clarify, levels=frame.levels, states=frame.states, count=frame.count)


def _no_context(expired: bool) -> NavigationResult:
    """Un seguimiento reconocible SIN contexto vigente: aclaración determinista (no inventa referencias ni cae al LLM libre)."""
    lead = (f"Pasaron más de {qctx.TTL_SECONDS // 60} minutos desde tu última consulta y no conservo el contexto. " if expired
            else "No tengo una consulta previa vigente para completar con eso. ")
    return NavigationResult(lead + _FU_HELP, (_ask("Ver el resumen del Catálogo", "¿Qué modelos tenemos?"), _ask("Elegir una métrica", "¿Qué entradas tienen mejor scoring?")))


def _run_spec(spec: qctx.QuerySpec, pack: str, stem: str) -> NavigationResult:
    """Ejecuta una consulta estructurada con los MISMOS ejecutores que las consultas directas (datos vigentes, nada reutilizado)."""
    if spec.capability == "summary":
        return _inventory_result(_InventoryIntent("summary", MAX_LIST, False, None, None), pack, stem)
    if spec.capability == "list":
        return _inventory_result(_InventoryIntent("list", spec.limit, False, spec.lob, spec.client, spec.offset, spec.state, spec.count), pack, stem)
    if spec.capability == "states":
        return _states_result(spec.client, spec.lob, pack, stem)
    if spec.capability == "groups":
        return _groups_result(spec.group_by or "client", spec.lob, spec.state, pack, stem, spec.client)
    if spec.capability == "rank":
        return _ranking_result(_RankIntent("rank", spec.indicator, spec.order, lob=spec.lob, client=spec.client, limit=spec.limit, offset=spec.offset,
                                           levels=spec.levels, state=spec.state, count=spec.count), pack, stem)
    return _ranking_result(_RankIntent("clarify_indicator", order=spec.order, lob=spec.lob, client=spec.client), pack, stem)         # "pending": la aclaración de métrica


def _canonical_again(spec: qctx.QuerySpec) -> tuple | None:
    """Opción «volver a consultar» con la pregunta canónica de la consulta (offset 0), fijada por el servidor."""
    labels = caps.lob_labels()
    lob_label, unclassified = labels.get(spec.lob or ""), spec.lob == dcs.UNCLASSIFIED
    state_phrase = query.STATE_BY_ID[spec.state].canonical if spec.state else ""
    if spec.capability == "rank" and spec.indicator:
        return _ask("Volver a consultar", indicators.canonical_question(spec.indicator, spec.order, client=spec.client, lob_label=lob_label, unclassified=unclassified,
                                                                        levels=spec.levels, state_phrase=state_phrase))
    where = _canonical_where(spec.client, spec.lob, labels)
    if spec.capability == "list":
        return _ask("Volver a consultar", _state_question(spec.state, spec.client, spec.lob, labels) if spec.state else f"Mostrame los modelos{where}")
    if spec.capability == "states":
        return _ask("Volver a consultar", f"¿Cuál es el estado de los assessments{where}?")
    if spec.capability == "groups":
        return _ask("Volver a consultar", f"¿Cuántos modelos hay {'por cliente' if spec.group_by == 'client' else 'por línea de negocio'}?")
    return None


def _reference_entry(f: _Follow, st: qctx.QueryState, pack: str, stem: str) -> tuple:
    """«Abrí el segundo» / «la confianza del segundo»: (entrada, posición, None) si la referencia se resuelve sobre la ÚLTIMA lista visible; (None, None, respuesta) si no. Solo se resuelve si el
    resultado recalculado es el MISMO (huella y entradas) y la entrada sigue existiendo."""
    if st.spec.capability not in ("list", "rank") or not st.refs:
        return None, None, NavigationResult("Todavía no hay una lista visible para elegir una entrada. " + _FU_HELP, (), keep_state=True)
    n = len(st.refs)
    if f.ordinal == 0:
        if n != 1:
            return None, None, NavigationResult(f"Hay {n} entradas visibles: decime cuál querés abrir (por ejemplo, «abrí el segundo»).", (), keep_state=True)
        position = 1
    else:
        position = n if f.ordinal == -1 else f.ordinal
    if not 1 <= position <= n:
        return None, None, NavigationResult(f"La lista visible tiene {n} entrada{'s' if n != 1 else ''}: no hay un {_ORDINAL_TEXT.get(position, str(position))}.", (), keep_state=True)
    current = _run_spec(st.spec, pack, stem)                                  # datos VIGENTES: la lista se recalcula con la misma consulta y la misma página
    again = _canonical_again(st.spec)
    if current.state is None:
        return None, None, NavigationResult("No pude revalidar la lista en este momento, así que no abro ninguna entrada para no abrir otra por error. Probá de nuevo en unos segundos.", (), keep_state=True)
    if current.state.fingerprint != st.fingerprint or current.state.refs != st.refs:
        return None, None, NavigationResult("La lista cambió desde que te la mostré (se actualizaron datos, clasificaciones o el orden), así que no abro ninguna entrada para no abrir otra por error. Volvé a pedirla para ver la lista vigente.",
                                            (again,) if again else ())
    kind, ref = st.refs[position - 1]
    entry = caps.find_entry(kind, ref)                                        # la identidad se revalida contra el Catálogo actual; la URL se reconstruye con los presenters, nunca se reutiliza
    if entry is None:
        return None, None, NavigationResult("Esa entrada ya no está disponible en el Catálogo. Volvé a pedir la lista para ver las vigentes.", (again,) if again else ())
    return entry, position, None


def _followup_reference(f: _Follow, st: qctx.QueryState, pack: str, stem: str) -> NavigationResult:
    """«Abrí el segundo»: se resuelve sobre la ÚLTIMA lista efectivamente visible (ver ``_reference_entry``). El modelo abierto queda EN FOCO sin cambiar la lista."""
    entry, position, error = _reference_entry(f, st, pack, stem)
    if error is not None:
        return error
    governed = caps.governed_read([entry])
    item = dataclasses.replace(caps.build_items([entry], governed, pack)[0], autoload=True)
    text = f"Abro el {_ORDINAL_TEXT.get(position, str(position))} de la lista que te mostré: **{item.title}**."
    return NavigationResult(text, (), caps.ResultSet("list", total=1, items=(item,)), state=qctx.QueryDraft(st.spec, st.refs, st.fingerprint, focus=entry.entry_id))


def _followup_page(f: _Follow, st: qctx.QueryState, pack: str, stem: str) -> NavigationResult:
    spec = st.spec
    if spec.capability in ("summary", "states", "groups"):
        return NavigationResult("El resumen no tiene páginas. Pedime un listado (por ejemplo, «mostrame 10 modelos») y después los siguientes.", (), keep_state=True)
    if spec.capability == "pending":
        pending = _run_spec(spec, pack, stem)
        return dataclasses.replace(pending, message="Todavía no hay una lista para recorrer: primero elegí una métrica. " + pending.message, keep_state=True)
    if f.op == "prev" and spec.offset == 0:
        return NavigationResult("Ya estás en la primera página.", (), keep_state=True)
    offset = spec.offset + spec.limit if f.op == "next" else max(0, spec.offset - spec.limit)
    page = _run_spec(dataclasses.replace(spec, offset=offset), pack, stem)     # se reconsultan los datos vigentes antes de paginar
    if page.state is not None and page.state.fingerprint != st.fingerprint:
        fresh = _run_spec(dataclasses.replace(spec, offset=0), pack, stem)     # el conjunto cambió: se reinicia desde la primera página para no duplicar ni omitir entradas
        return dataclasses.replace(fresh, message="Los datos cambiaron desde que te mostré la lista; vuelvo a la primera página para no duplicar ni omitir entradas. " + fresh.message)
    return page


def _followup_change(f: _Follow, st: qctx.QueryState, pack: str, stem: str) -> NavigationResult:
    spec = st.spec
    if len(f.clients) > 1 or len(f.lobs) > 1:
        return NavigationResult("Nombraste más de un cliente o más de una línea de negocio. Pedime un solo cliente y una sola línea de negocio por vez.", (), keep_state=True)
    client = f.clients[0] if f.clients else spec.client
    lob = f.lobs[0] if f.lobs else spec.lob
    labels = caps.lob_labels()
    if len(f.states) > 1:
        return dataclasses.replace(_states_clarification(query.QueryFrame(states=f.states), client, lob, labels), keep_state=True)
    state = f.states[0] if f.states else spec.state                                                 # el estado pertenece al ALCANCE: se conserva salvo que se pida otro
    count = spec.count or f.count
    if len(f.inds) > 1:
        level_map = tuple((i, tuple(lv for owner, lv in f.levels if owner == i)) for i in f.inds if any(owner == i for owner, _lv in f.levels))
        return _ranking_result(_RankIntent("multi", candidates=f.inds, client=client, lob=lob, state=state, level_map=level_map), pack, stem)
    if f.inds:
        ind = indicators.BY_ID[f.inds[0]]
        if f.clarify_direction:
            return _ranking_result(_RankIntent("clarify_direction", indicator=ind.id, client=client, lob=lob, quality_words=True), pack, stem)
        if any(owner != ind.id for owner, _lv in f.levels) or (f.levels and not ind.levels):          # un nivel que el indicador no tiene: se aclara, nunca se ignora
            frame = query.QueryFrame(indicators=f.inds, levels=f.levels)
            return dataclasses.replace(_levels_clarification(ind, frame, client, lob, labels, ()), keep_state=True)
        levels = tuple(lv for lv in ind.levels if any(owner == ind.id and lv == l2 for owner, l2 in f.levels))      # los niveles pedidos AHORA; los del indicador anterior NO sobreviven al cambio de indicador
        new = qctx.QuerySpec("rank", client, lob, ind.id, f.order, spec.limit, 0, levels=levels, state=state, count=count)
    else:
        capability = "list" if spec.capability == "summary" else spec.capability                 # un filtro sobre el resumen es un listado filtrado
        if capability == "states" and f.states:
            capability = "list"                                                                  # «¿y los desactualizados?» tras la distribución: el listado de ese estado
        new = dataclasses.replace(spec, capability=capability, client=client, lob=lob, state=state, offset=0, count=count)
    return _run_spec(new, pack, stem)


def _followup_count(st: qctx.QueryState, pack: str, stem: str) -> NavigationResult:
    """«¿Cuántos son?»: la MISMA consulta vigente, recalculada con datos vigentes, con el encabezado que contesta el número primero (D-4)."""
    spec = st.spec
    if spec.capability == "pending":
        pending = _run_spec(spec, pack, stem)
        return dataclasses.replace(pending, message="Todavía no hay una lista para contar: primero elegí una métrica. " + pending.message, keep_state=True)
    return _run_spec(dataclasses.replace(spec, count=True, offset=0), pack, stem)


# ── Modelo EN FOCO: «¿y su confianza?», «¿por qué tiene Buena?», «abrí su Assessment», «la confianza del segundo» ───────────────────────────────────────────────
# El foco es la IDENTIDAD (`entry_id`) del último modelo consultado, abierto por referencia o navegado: se guarda con la consulta (`QueryState.focus`), vence con ella (15 min, mismo usuario y conversación) y
# se REVALIDA contra el Catálogo vigente en cada uso; los valores se vuelven a leer siempre (nunca se reutiliza nada de la respuesta anterior). Una consulta nueva o cualquier otra respuesta lo borra.
_DEIXIS = re.compile(r"\b(?:su|sus|ese|esa|este|mismo|misma|esta (?:modelo|entrada|flujo|evaluacion|cobertura|confianza|adopcion))\b")          # «esta» sola es «está» sin tilde: no es deixis
_OPEN_OWN = re.compile(r"^(?:y )?(?:(?:me )?(?:abri|abre|abrime|ver|veo|mostra|mostrame|muestrame|quiero ver|necesito ver|llevame a|ir a)\s+)(?:(?:el|la|al)\s+)?(?:su|ese|este|esa|esta)\s+(?:assessment|evaluacion|informe)\b")
_ORD_ATTR = re.compile(r"^(?:y )?(?P<attr>.+?)\s+(?:del|de el|de la)\s+(?P<w>" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r"|ultim[oa]|\d{1,2}(?:o|a)?)$")


def _focus_draft(entry_id: str, attrs: tuple[str, ...] = ()) -> qctx.QueryDraft:
    """El contexto de una respuesta sobre UN modelo: su identidad como foco y los indicadores pedidos (parámetros, nunca valores)."""
    return qctx.QueryDraft(qctx.QuerySpec("entry", entry=entry_id, attrs=attrs), ((caps.CATALOG_ENTRY, entry_id),), "", focus=entry_id)


def _no_focus(expired: bool) -> NavigationResult:
    lead = (f"Pasaron más de {qctx.TTL_SECONDS // 60} minutos desde tu última consulta y no conservo el modelo del que hablábamos. " if expired
            else "No tengo un modelo en foco para completar con eso. ")
    return NavigationResult(lead + "Decime el nombre del modelo, por ejemplo «¿Qué sabés de Delivery Document?».", ())


def _focus_entry_missing() -> NavigationResult:
    return NavigationResult("Ese modelo ya no está disponible en el Catálogo, así que no te muestro datos de él. Volvé a consultarlo por su nombre o pedime la lista vigente.", ())


def _focus_followup(question: str, pack: str, stem: str, ctx: qctx.QueryContext) -> NavigationResult | None:
    """Seguimiento sobre el modelo en foco. Solo se reclama un mensaje CORTO con una pregunta individual (atributo, vigencia, fundamentos) marcada como continuación («y…», «su…», «ese…», o un «por qué»)
    o el pedido de abrir SU Assessment; un nombre de modelo, un cliente o una línea de negocio nuevos lo hacen una consulta nueva."""
    t = re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", norm_text(question))).strip()
    words = t.split()
    if not words or len(words) > 9:
        return None
    open_own = bool(_OPEN_OWN.match(t))
    intent = None if open_own else answers.parse_intent(question)
    deictic = bool(_DEIXIS.search(t))
    if not open_own and intent is None:
        return None
    status = ctx.status()
    live = ctx.live_state() if status == qctx.LIVE else None
    entity_list = live is not None and live.spec.capability in ("list", "rank")                    # el foco vino de abrir una referencia: sin deixis, «¿y por confianza?» sigue siendo de la lista
    continuation = deictic or (intent is not None and (words[0] == "y" or intent.why) and not entity_list)
    if not (open_own or continuation):
        return None
    no_focus = live is None or not live.focus
    bare_why = intent is not None and intent.why and (bool(intent.levels) or len(words) <= 4)         # «¿por qué tiene Buena?», «¿y por qué?»: sin modelo nombrado, se refieren al foco
    if no_focus and not (open_own or deictic or bare_why):
        return None
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    clients = catalog_navigation.clients(mod, entries) if mod else []
    padded = f" {t} "
    if any(_word(c, padded) for c in clients) or _lobs_named(t):
        return None                                                          # nombró otro alcance: es una consulta nueva
    governed = caps.governed_read(flows) if flows else None
    if governed is not None and _resolve_entry(question, flows, clients, governed).status != resolver.NONE:
        return None                                                          # nombró un modelo: lo resuelve la consulta directa
    if no_focus:
        return _no_focus(status == qctx.EXPIRED)
    entry = caps.find_entry(caps.CATALOG_ENTRY, live.focus)                  # el foco se revalida contra el Catálogo vigente
    if entry is None or governed is None or entry.entry_id not in governed.statuses:
        return _focus_entry_missing()
    try:
        if open_own:
            item = caps.build_items([entry], governed, pack)[0]
            label = f"{entry.cliente} · {item.title}".strip(" ·")
            out = NavigationResult(f"Acá está el **Assessment** de **{label}**: se abre el informe tal como figura hoy en el Catálogo (o su estado, si todavía no se evaluó).",
                                   (("Ver Assessment", presenter.assessment_url(entry, pack)),), state=_focus_draft(entry.entry_id))
        else:
            out = _entry_answer(intent, entry, governed, pack)
        if entity_list:                                                      # el foco vino de una lista: la lista sigue valiendo («abrí el tercero») y el modelo consultado queda en foco
            return dataclasses.replace(out, state=qctx.QueryDraft(live.spec, live.refs, live.fingerprint, focus=entry.entry_id))
        return out
    except Exception:  # noqa: BLE001 — un seguimiento RECONOCIDO nunca cae en silencio al LLM libre: si falla la lectura, se dice
        log.exception("seguimiento sobre el modelo en foco: falló la respuesta")
        return NavigationResult(_FAILED_QUERY, (), keep_state=True)


def _ordinal_attribute(question: str, ctx: qctx.QueryContext, pack: str, stem: str) -> NavigationResult | None:
    """«La confianza del segundo»: el atributo de una entrada de la ÚLTIMA lista visible, solo si la lista recalculada es la misma (huella y entradas) y la entrada sigue existiendo."""
    t = re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", norm_text(question))).strip()
    m = _ORD_ATTR.match(t)
    if not m:
        return None
    intent = answers.parse_intent(m.group("attr"))
    if intent is None:
        return None
    status = ctx.status()
    if status != qctx.LIVE:
        return _no_context(expired=status == qctx.EXPIRED)
    st = ctx.state
    w = m.group("w")
    f = _Follow("ref", ordinal=-1 if w.startswith("ultim") else _ORDINALS.get(w) or int(re.match(r"\d+", w).group()))
    try:
        found, position, error = _reference_entry(f, st, pack, stem)
        if error is not None:
            return error
        governed = caps.governed_read([found])
        if governed is None:
            return NavigationResult(_FAILED_QUERY, (), keep_state=True)
        out = _entry_answer(intent, found, governed, pack)
        return dataclasses.replace(out, state=qctx.QueryDraft(st.spec, st.refs, st.fingerprint, focus=found.entry_id))           # la lista sigue valiendo («el tercero») y el modelo queda en foco
    except Exception:  # noqa: BLE001
        log.exception("atributo de una referencia de la lista: falló la respuesta")
        return NavigationResult(_FAILED_QUERY, (), keep_state=True)


def _entry_context_help() -> NavigationResult:
    return NavigationResult("La última consulta fue sobre un solo modelo, así que no hay una lista para filtrar, contar o recorrer. Para eso pedime una consulta de conjunto, por ejemplo "
                            "«¿Qué modelos tienen adopción buena?»; para otro atributo del mismo modelo, «¿y su confianza?».", (), keep_state=True)


def _catalog_followup(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
    ctx = ctx or qctx.QueryContext(None, "", 0.0)
    focused = _focus_followup(question, pack, stem, ctx) or _ordinal_attribute(question, ctx, pack, stem)          # modelo en foco / atributo de una referencia (paso 4B.4)
    if focused is not None:
        return focused
    words = norm_text(question).strip().split()
    is_count = bool(_FU_COUNT.match(re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,;:]", " ", norm_text(question))).strip()))
    if not words or len(words) > 9 or (words[0].strip("¿¡") in _FU_INTERROGATIVES and not is_count):
        return None                                                          # compuerta barata: la mayoría de las preguntas no son seguimientos y no pagan la carga del Catálogo
    ctx = ctx or qctx.QueryContext(None, "", 0.0)
    status = ctx.status()
    mod, entries = catalog_reads._catalog_entries()
    clients = catalog_navigation.clients(mod, entries) if mod else []
    f = _followup_intent(question, clients, status == qctx.LIVE)
    if f is None:
        return None
    if f.op == "reset":
        return NavigationResult("Listo, empezamos de nuevo: no conservo la consulta anterior. " + _FU_HELP, (_ask("Ver el resumen del Catálogo", "¿Qué modelos tenemos?"), _ask("Elegir una métrica", "¿Qué entradas tienen mejor scoring?")))
    if status != qctx.LIVE:
        if f.op == "change" and (_ranking_intent(question, clients) is not None or _inventory_intent(question, clients) is not None
                                 or ((f.states or f.levels) and (f.clients or f.lobs))):
            return None                                                      # trae su propia consulta completa: la resuelve el destino directo
        return _no_context(expired=status == qctx.EXPIRED)
    st = ctx.state
    try:
        if st.spec.capability == "entry" and f.op != "ref":
            return _entry_context_help()                                      # la consulta vigente es sobre UN modelo: no hay lista que paginar, contar o filtrar
        if f.op in ("next", "prev"):
            return _followup_page(f, st, pack, stem)
        if f.op == "ref":
            return _followup_reference(f, st, pack, stem)
        if f.op == "count":
            return _followup_count(st, pack, stem)
        return _followup_change(f, st, pack, stem)
    except Exception:  # noqa: BLE001 — un seguimiento RECONOCIDO nunca cae en silencio al LLM libre: si falla la ejecución, se dice
        log.exception("seguimiento del asistente: falló la ejecución de %s", f.op)
        return NavigationResult(_FAILED_QUERY, (), keep_state=True)


TARGETS: tuple[NavigationTarget, ...] = (NavigationTarget("catalog_followup", _catalog_followup), NavigationTarget("client_report", _client_report), NavigationTarget("data_catalog", _data_catalog),
                                         NavigationTarget("entry_query", _entry_query), NavigationTarget("catalog_query", _catalog_query), NavigationTarget("entry_assessment", _entry_assessment),
                                         NavigationTarget("catalog_ranking", _catalog_ranking), NavigationTarget("catalog_inventory", _catalog_inventory))


def resolve(question: str, pack: str, stem: str = "", ctx: qctx.QueryContext | None = None) -> NavigationResult | None:
    """Primer destino que reconoce la intención de navegación; None si la pregunta no es de navegación (sigue el flujo normal del asistente)."""
    for target in TARGETS:
        try:
            result = target.resolve(question, pack, stem, ctx)
        except Exception:  # noqa: BLE001 — un fallo de navegación nunca rompe el asistente: cae al flujo normal
            result = None
        if result is not None:
            return result
    return None
