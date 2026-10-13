"""Capacidades de consulta estructurada del asistente: contrato TRANSVERSAL de resultados navegables + ejecución del inventario del Catálogo (su primera familia de recursos).

El asistente RESUME, ayuda a acotar y lleva al Catálogo; la consulta (qué entradas, en qué orden, con qué clasificación) se ejecuta acá, de forma determinista y SOLO con los contratos
gobernados existentes, y devuelve un ``ResultSet`` independiente del render. El texto de la respuesta y las filas del chat se construyen aparte (``assistant_navigation`` y la plantilla
``partials/assistant_result_rows.html``).

Reutiliza, sin copiar nada: ``catalog_navigation`` (universo navegable y orden por mirada Danwor), ``dcs.effective_lob_by_entry`` (clasificación EFECTIVA, por lote) y el resolver de
títulos del propio Catálogo (``presenter.entry_title`` / ``presenter.flow_heading``). No hay inventario, clasificación ni persistencia propios, y las URLs salen SIEMPRE del ``entry_id``
real de la entrada (``presenter.flow_url``), nunca de texto.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from webapp.safe_redirect import safe_internal_next
from webapp.services import catalog_navigation, catalog_reads
from webapp.services import domain_classification_service as dcs

log = logging.getLogger("danwor.assistant_capabilities")


# ── Contrato transversal de resultados navegables ───────────────────────────────────────────────────────────────────────────────
# Una fila = UNA cosa que el usuario puede abrir, de cualquier familia de recursos (entradas del Catálogo, recursos curados del Hub…). Lo UNIVERSAL es la identidad compuesta `(kind, ref)`,
# el título, el subtítulo y la acción de navegación (`url` + `mode`). Lo propio de una familia va en un bloque OPCIONAL compuesto (`catalog`), nunca como campo obligatorio del resto.
# El render solo conoce lo universal: no depende de ninguna familia.
CATALOG_ENTRY = "catalog_entry"            # entrada navegable del Catálogo (ref = entry_id)
CURATED_RESOURCE = "curated_resource"      # recurso curado del Hub (`intent_assets`; ref = módulo + huella estable del destino)

MODE_EMBED, MODE_LINK, MODE_EXTERNAL = "embed", "link", "external"
MODES = (MODE_EMBED, MODE_LINK, MODE_EXTERNAL)
# embed    → panel central por HTMX (mecanismo existente de las acciones del asistente); solo rutas internas.
# link     → navegación normal en la misma pestaña a una ruta interna (cada ruta destino conserva su control de sesión).
# external → pestaña NUEVA con rel="noopener noreferrer": URL absoluta http(s) o un recurso del pack (`/pack-resources/…`, ruta interna con sesión que se abre fuera de la página).
EXTERNAL_INTERNAL_PREFIX = "/pack-resources/"


def is_internal_path(url: str) -> bool:
    """Ruta interna segura. La regla de «qué es interno» es la AUTORIDAD ÚNICA del Hub (`webapp.safe_redirect.safe_internal_next`: UNA barra, nunca «//», sin barras invertidas ni lo que el browser
    normalizaría); acá solo se exige que la URL ya sea EXACTAMENTE su propia forma segura y que no lleve espacios ni caracteres de control. No se duplica la validación."""
    return bool(url) and safe_internal_next(url) == url and not any(ord(c) <= 32 for c in url)


def is_external_url(url: str) -> bool:
    """URL absoluta http(s) con host, sin caracteres de control."""
    parts = urlsplit(url)
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc) and not any(ord(c) < 32 or c.isspace() for c in url)


def valid_target(mode: str, url: str) -> bool:
    """Un destino es válido para su modo: `embed`/`link` solo rutas internas; `external` solo URL http(s) o recursos del pack."""
    if mode in (MODE_EMBED, MODE_LINK):
        return is_internal_path(url)
    if mode == MODE_EXTERNAL:
        return is_external_url(url) or (is_internal_path(url) and url.startswith(EXTERNAL_INTERNAL_PREFIX))
    return False


@dataclass(frozen=True)
class Evidence:
    """De dónde sale un valor: la fuente autoritativa, cuándo se generó y con qué reglas (solo lectura de contratos gobernados; nada se recalcula)."""
    source: str
    generated_at: str | None = None     # fecha legible del Assessment o evaluación que lo respalda
    ruleset: str | None = None          # versión de las reglas (Juicio, portabilidad…)
    url: str | None = None              # informe/detalle donde se ve la evidencia completa (se reconstruye con los presenters, nunca de texto)


@dataclass(frozen=True)
class Metric:
    """Valor de UN indicador gobernado sobre una entrada, con su evidencia. `value` es el valor crudo de la fuente (un nivel ordinal NO se convierte en número)."""
    indicator: str
    value: Any
    display: str
    evidence: Evidence | None = None


@dataclass(frozen=True)
class CatalogAttrs:
    """Atributos propios de una entrada del Catálogo (no existen en otras familias)."""
    client: str
    lob_label: str          # línea de negocio EFECTIVA («Sin clasificar» / «Clasificación no disponible» incluidos); "" si la clasificación no se pudo leer
    lob_source: str         # MANUAL | AUTOMATIC | UNCLASSIFIED | UNAVAILABLE | "" (clasificación no leída)
    metrics: tuple[Metric, ...] = ()        # indicadores gobernados consultados (vacío en un inventario)


@dataclass(frozen=True)
class ResultItem:
    kind: str               # familia del recurso (CATALOG_ENTRY, CURATED_RESOURCE…)
    ref: str                # identidad estable DENTRO de la familia; la identidad global es `(kind, ref)`
    title: str              # título legible
    subtitle: str = ""      # línea secundaria legible (p. ej. «cliente · línea de negocio» o el módulo de origen)
    url: str = ""           # destino: SIEMPRE sale de la identidad real del recurso, nunca del texto de la consulta
    mode: str = MODE_EMBED
    catalog: CatalogAttrs | None = None     # solo en entradas del Catálogo
    badge: str = ""         # valor corto destacado en la fila (p. ej. «Buena», «100.0%», «88/100»); lo decide quien arma el resultado, la fila solo lo muestra
    note: str = ""          # evidencia/aclaración corta bajo el valor (fuente · reglas · fecha)
    autoload: bool = False  # abrir el detalle al renderizarse (solo `embed`): lo usa una referencia resuelta («abrí el segundo»), con la URL reconstruida desde el recurso vigente

    def __post_init__(self):
        if not self.kind or not self.ref:
            raise ValueError("un resultado necesita kind y ref")
        if not valid_target(self.mode, self.url):
            raise ValueError(f"destino inválido para el modo {self.mode!r}")
        if self.autoload and self.mode != MODE_EMBED:
            raise ValueError("solo un destino embed puede abrirse solo")

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.ref)


@dataclass(frozen=True)
class CatalogSummary:
    """Resumen propio de consultas sobre el Catálogo (distribución por clasificación)."""
    aggregates: tuple[tuple[str, str, int], ...] = ()    # (grupo, rótulo, n): distribución por línea de negocio EFECTIVA; es pertenencia, no un ranking
    classified: bool = True                              # False si la clasificación efectiva no se pudo leer (jamás se reemplaza por una supuesta)


@dataclass(frozen=True)
class RankingInfo:
    """Cómo se ordenó un resultado por UN indicador: universo, cobertura, exclusiones, empates y advertencia metodológica (para explicarlo sin inventar conclusiones)."""
    indicator: str
    label: str
    order: str                                        # "best_first" | "worst_first"
    order_text: str                                   # «de mejor a peor», «de más a menos»…
    universe: int                                     # entradas del alcance (tras filtros)
    evaluated: int                                    # entradas con valor comparable (las únicas que se ordenan)
    excluded: tuple[tuple[str, int], ...] = ()        # (motivo, n) de lo que NO se compara
    ties: tuple[tuple[str, int], ...] = ()            # (valor mostrado, n) de los empates visibles
    cut_in_tie: tuple[str, int] | None = None         # el corte de filas cae dentro de un empate: (valor, entradas que quedaron fuera)
    caveat: str = ""
    page: int = 1                                     # página mostrada (1-based) y total de páginas con valor comparable
    pages: int = 1
    continues_tie: tuple[str, int] | None = None      # esta página CONTINÚA un empate de la anterior: (valor, entradas con ese valor en páginas previas)


@dataclass(frozen=True)
class ResultSet:
    capability: str                                      # "summary" | "list" | "rank"
    total: int                                           # elementos del universo tras los filtros
    items: tuple[ResultItem, ...] = ()                   # ya acotado; vacío en un resumen
    footer: str = ""                                     # nota en texto plano bajo las filas
    catalog: CatalogSummary | None = None                # solo en consultas sobre el Catálogo
    ranking: RankingInfo | None = None                   # solo en resultados ordenados por un indicador
    fingerprint: str = ""                                # huella del conjunto ORDENADO completo (todas las páginas) que originó la lista; validador de referencias y de paginación


@dataclass(frozen=True)
class Governed:
    """Lectura gobernada de un lote de entradas: estados de Assessment (títulos) y clasificación efectiva, leídos UNA vez."""
    statuses: dict
    eff: dict


def find_entry(kind: str, ref: str):
    """La entrada VIGENTE del Catálogo que corresponde a una identidad `(kind, ref)`, o None si ya no existe (o la familia no se resuelve acá). La referencia se revalida contra el universo navegable
    actual: no se confía en nada guardado (ni URLs ni títulos)."""
    if kind != CATALOG_ENTRY:
        return None
    mod, entries = catalog_reads._catalog_entries()
    flows = catalog_navigation.browsable_flows(mod, entries) if mod else []
    return next((e for e in flows if e.entry_id == ref), None)


def lob_labels() -> dict[str, str]:
    """id de línea de negocio → rótulo (taxonomía gobernada, «Sin clasificar» incluido) más el rótulo de «no disponible»."""
    labels = {o["id"]: o["label"] for o in dcs.lob_options()}
    labels[dcs.UNAVAILABLE] = "Clasificación no disponible"
    return labels


def governed_read(flows: list) -> Governed | None:
    """Estados + clasificación efectiva por lote; None si no se pudo leer: la clasificación nunca rompe el asistente y jamás se reemplaza por una supuesta."""
    try:
        from webapp.services import assessment_service
        statuses = assessment_service.statuses_for_entries(flows)
        return Governed(statuses, dcs.effective_lob_by_entry(flows, statuses))
    except Exception:  # noqa: BLE001 — se informa como no disponible
        log.exception("inventario del asistente: no se pudo leer la clasificación efectiva")
        return None


def order_entries(scope: list) -> list:
    """Orden determinista: mirada Danwor (plena → parcial), luego cliente, nombre, consumo y ``entry_id`` (independiente del orden de carga)."""
    return sorted(scope, key=lambda e: (catalog_navigation._VERDICT_RANK.get(e.veredicto, 9), (e.cliente or "").lower(), (e.dominio or "").lower(),
                                        [c.lower() for c in e.consumo], e.entry_id))


def distribution(scope: list, eff: dict, labels: dict[str, str]) -> tuple[tuple[str, str, int], ...]:
    """(grupo, rótulo, n) por cantidad (desc) y orden de la taxonomía; «sin clasificar» y «no disponible» siempre al final y rotulados como tales."""
    counts: dict[str, int] = {}
    for e in scope:
        g = eff[e.entry_id]["group"]
        counts[g] = counts.get(g, 0) + 1
    order = list(labels)
    lines = [(g, labels[g], counts[g]) for g in sorted((g for g in counts if g not in (dcs.UNCLASSIFIED, dcs.UNAVAILABLE)), key=lambda g: (-counts[g], order.index(g)))]
    for g in (dcs.UNCLASSIFIED, dcs.UNAVAILABLE):
        if counts.get(g):
            lines.append((g, labels[g], counts[g]))
    return tuple(lines)


def _title(entry: Any, governed: Governed | None) -> str:
    """Título legible del modelo con el resolver del Catálogo; sin lectura gobernada, el resolver del propio Catálogo por artefacto de consumo. Nunca el dominio legado."""
    from webapp.services import assessment_presenter as presenter
    try:
        if governed is not None:
            return presenter.entry_title(governed.statuses[entry.entry_id], entry)[0] or presenter.NO_TERMINAL_TITLE
        return presenter.flow_heading(entry)[0]
    except Exception:  # noqa: BLE001 — un título nunca rompe la respuesta
        log.exception("inventario del asistente: no se pudo resolver el título de %s", getattr(entry, "entry_id", "?"))
        return presenter.NO_TERMINAL_TITLE


def build_items(entries: list, governed: Governed | None, pack: str) -> tuple[ResultItem, ...]:
    """Filas de las entradas MOSTRADAS (≤ MAX_LIST). Identidad = ``entry_id``; título y línea de negocio, de los contratos gobernados."""
    from webapp.services import assessment_presenter as presenter
    items = []
    for e in entries:
        lob = presenter.lob_display(governed.eff[e.entry_id]) if governed is not None else None
        attrs = CatalogAttrs(client=str(e.cliente or ""), lob_label=lob["label"] if lob else "", lob_source=lob["source"] if lob else "")
        items.append(ResultItem(
            kind=CATALOG_ENTRY, ref=e.entry_id, title=_title(e, governed),
            subtitle=" · ".join(x for x in (attrs.client, attrs.lob_label) if x),
            url=presenter.flow_url(e, pack), mode=MODE_EMBED, catalog=attrs))
    return tuple(items)
