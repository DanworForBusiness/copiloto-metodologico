from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from core.prompts import get_out_of_scope_redirect
from core.routing_scorers import (
    _display_heading,
    _find_cross_module_asset,
    _find_cross_module_section,
    _find_matching_asset,
    _find_matching_assets,
    _find_matching_section,
    _is_asset_seeking_query,
    _is_conceptual_or_comparative_query,
    _query_object_tokens,
    _should_pair_section_with_asset,
)
from core.utils import norm_text, slugify
from webapp.services.content_service import (
    ModuleDocument,
    list_appendices,
    list_modules,
)


@dataclass(frozen=True)
class AssistantRouteAction:
    label: str
    url: str
    external: bool = False
    # embed=True: el detalle de catálogo (flujo/conexión) se carga por HTMX en
    # #catalog-detail-slot —igual que un card manual del catálogo— en vez de navegar
    # a la página standalone. Así el módulo queda debajo (catalog-detail-open) y los
    # botones "← Volver" (dwCatalogBack) y "Abrir CDS View" (que apunta al slot)
    # funcionan; con navegación completa no había slot ni ese modo y quedaban muertos.
    embed: bool = False


@dataclass(frozen=True)
class AssistantRoute:
    message: str
    actions: tuple[AssistantRouteAction, ...]
    original_question: str
    explain_module: str = ""
    explain_heading: str = ""
    # Directiva para "Explicame vos" cuando el usuario optó por la respuesta GENERAL
    # tras un lead de catálogo: desactiva el grounding interno y pide mirada a nivel
    # producto SAP Datasphere (si no, el LLM parrotea el "no" del catálogo). "" = sin
    # directiva (comportamiento normal, p. ej. explain de una sección con su contexto).
    explain_prompt: str = ""
    # True sólo para el affordance de "sin evidencia local en ningún módulo"
    # (ver AssistantRoutingService.no_local_evidence_explain): le pide a
    # AssistantChatService.ask() una respuesta SIN el scope restrictivo del
    # módulo activo (ver ask(general=True)), en vez de reusar explain_module
    # como "ese módulo, con directiva" -- acá no hay ningún módulo correcto
    # al que anclarse.
    explain_general: bool = False


def _module_url(stem: str, pack_name: str, heading: str = "") -> str:
    fragment = f"#{quote(slugify(_display_heading(heading)))}" if heading else ""
    return f"/modules/{quote(stem)}?pack={quote(pack_name)}{fragment}"


def _asset_action(asset: dict, pack_name: str) -> AssistantRouteAction | None:
    raw = str(asset.get("asset") or asset.get("url") or "").strip()
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://")):
        return AssistantRouteAction("Abrir recurso", raw, True)
    normalized = raw.replace("\\", "/").lstrip("./")
    kind, separator, relative = normalized.partition("/")
    if not separator or kind not in {"assets", "templates"}:
        kind, relative = "assets", normalized
    url = f"/pack-resources/{quote(pack_name)}/{kind}/{quote(relative, safe='/')}"
    return AssistantRouteAction("Abrir recurso", url, True)


def _module_stem_for_number(number: str, pack_name: str) -> str:
    target = str(number or "").zfill(2)
    summaries = (*list_modules(pack_name), *list_appendices(pack_name))
    match = next((item for item in summaries if item.number == target), None)
    return match.stem if match else ""


_COMPARATIVE_TERMS = (
    "diferencia", "diferencias", "comparar", "comparativa", "comparativo",
    "compatibilidad", "compatible", "vs", "versus", "entre", "puede consumir",
    "pueden consumir", "puede exponer", "pueden exponer",
)

_DIRECT_ASSET_TERMS = (
    "como conectar", "como config", "cómo conectar", "cómo config", "paso a paso",
    "how-to", "guia", "guía", "abrir recurso", "pasame", "dame el link",
    "descargar", "documentacion", "documentación", "link", "recurso",
)

_CONCEPT_GROUPS = {
    "power_bi": ("power bi", "powerbi"),
    "odbc": ("odbc",),
    "odata": ("odata", "o data"),
    "modelos_analiticos": (
        "modelo analitico", "modelos analiticos", "modelo analítico",
        "modelos analíticos", "analytical model", "analytical dataset",
    ),
}


def _concept_groups_in(text: str) -> set[str]:
    normalized = norm_text(text)
    return {
        group for group, variants in _CONCEPT_GROUPS.items()
        if any(variant in normalized for variant in variants)
    }


def _is_conceptual_asset_question(question: str) -> bool:
    normalized = norm_text(question)
    if any(term in normalized for term in _DIRECT_ASSET_TERMS):
        return False
    groups = _concept_groups_in(question)
    has_comparison = any(term in normalized for term in _COMPARATIVE_TERMS)
    return has_comparison and len(groups) >= 2


def _asset_covers_question_axes(question: str, asset: dict) -> bool:
    question_groups = _concept_groups_in(question)
    if len(question_groups) < 2:
        return True
    asset_text = " ".join(
        str(part or "")
        for part in (
            asset.get("description"),
            " ".join(asset.get("keywords") or []),
            asset.get("language"),
        )
    )
    return question_groups <= _concept_groups_in(asset_text)


def _reporting_section_hint(question: str, pack_name: str) -> dict | None:
    normalized = norm_text(question)
    reporting_terms = (
        "power bi", "powerbi", "sac", "odbc", "odata", "reporting",
        "modelo analitico", "modelos analiticos", "modelo analítico", "modelos analíticos",
    )
    if not any(term in normalized for term in reporting_terms):
        return None
    heading = "Conexión de Herramientas Externas" if any(
        term in normalized for term in ("odbc", "odata", "conexion", "conexión")
    ) else "Estrategia de Reporting"
    stem = _module_stem_for_number("05", pack_name)
    if not stem:
        return None
    module_label = next((item.nav_label for item in list_modules(pack_name) if item.stem == stem), stem)
    return {"module": stem, "module_label": module_label, "heading": heading}


def _related_asset_action(asset: dict, pack_name: str) -> AssistantRouteAction | None:
    action = _asset_action(asset, pack_name)
    if not action:
        return None
    return AssistantRouteAction("Abrir recurso relacionado", action.url, action.external)


def _is_current_section(heading: str, module: str, stem: str, active_section: str) -> bool:
    """True si (module, heading) es la sección donde el usuario YA está parado.
    Skip posicional: no re-recomendamos la sección actual — caemos al siguiente
    candidato (recurso, cross-module) o al fallback "ya estás acá"."""
    return bool(active_section) and module == stem and slugify(_display_heading(heading)) == active_section


def _catalog_action(pack_name: str) -> AssistantRouteAction | None:
    """Botón al catálogo de dominios (módulo 04, pestaña Catálogo) para el
    repertorio del fallback. Pilot: el catálogo hoy vive en el módulo 04."""
    mod = next((m for m in list_modules(pack_name) if m.stem.startswith("04_")), None)
    if not mod:
        return None
    return AssistantRouteAction(
        "Ver catálogo de dominios",
        f"/modules/{quote(mod.stem)}?pack={quote(pack_name)}&tab=catalogo",
    )


_AVAILABILITY_TERMS = (
    "tenemos", "tienen", "tenes", "tenés", "hay ", "existe", "contamos con",
    "disponemos", "ya hay", "ya tenemos", "esta disponible", "está disponible",
    "disponible", "algun caso", "algún caso", "algun cliente", "algún cliente",
)


def _is_availability_query(question: str) -> bool:
    """Consulta de disponibilidad/evidencia: '¿tenemos X?', '¿hay X?', '¿existe X?'.
    Para estas, un artefacto REAL del catálogo es mejor respuesta que un template."""
    q = norm_text(question)
    return any(term in q for term in _AVAILABILITY_TERMS)


_HOWTO_VERBS = (
    "configurar", "configuro", "instalar", "conectar", "conecto", "transportar",
    "importar", "activar", "integrar",
)


def _is_howto(question: str) -> bool:
    """How-to real (instrucción operativa), guard para los filtros/inventarios de
    catálogo. Preciso a propósito: usa "como" + VERBOS COMPLETOS, no los roots amplios
    de _is_asset_seeking_query — que dan falso positivo en SUSTANTIVOS de inventario
    (conect·ores, model import, referencia·s) y bloqueaban consultas de catálogo."""
    q = norm_text(question)
    if q.startswith("como ") or " como " in f" {q} ":
        return True
    # verbos por PALABRA COMPLETA (no substring): "conecto" NO debe matchear dentro de
    # "conectores", ni "importar" dentro de "model import".
    return bool(set(re.findall(r"[a-z]+", q)) & set(_HOWTO_VERBS))


def _catalog_module():
    """Importa `catalog_service` (vive en tools/catalog) de forma perezosa y segura.
    Devuelve (módulo, cat_dir) o (None, "") si no está disponible."""
    import os as _os
    import sys as _sys
    from config import BASE_DIR
    svc_dir = str(BASE_DIR / "tools" / "catalog")
    if svc_dir not in _sys.path:
        _sys.path.insert(0, svc_dir)
    import catalog_service as _cat
    cat_dir = _os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
    return _cat, cat_dir


def _find_matching_catalog(question: str, pack_name: str):
    """Mejor flujo del catálogo (evidencia real publicada) para la consulta, o None.
    Lee catalog_local (runtime); si está vacío/ausente → None y el router cae a su
    comportamiento normal (template/sección). El catálogo NUNCA rompe el routing."""
    try:
        _cat, cat_dir = _catalog_module()
        entries = _cat.load_catalog(cat_dir)
        return _cat.best_domain_match(question, entries) if entries else None
    except Exception:  # noqa: BLE001 — el catálogo nunca debe romper el routing
        return None


def _list_cds_names() -> list[str]:
    """Nombres de las CDS views publicadas en el catálogo de APIs (APIs/<name>.md).
    Lee catalog_local (runtime); [] si no existe. Nunca rompe el routing."""
    try:
        import os as _os
        from config import BASE_DIR
        cat_dir = _os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        apis_dir = _os.path.join(cat_dir, "APIs")
        if not _os.path.isdir(apis_dir):
            return []
        return [f[:-3] for f in _os.listdir(apis_dir) if f.endswith(".md")]
    except Exception:  # noqa: BLE001 — el catálogo nunca debe romper el routing
        return []


def _find_matching_cds(question: str):
    """Nombre técnico de una CDS view publicada mencionado en la consulta, o None.
    Señal muy precisa: si la pregunta nombra una CDS del catálogo de APIs (ej.
    'ZCDS_DSP_FACT_MCHB', o el artefacto con prefijo '1TR_ZCDS_...'), la respuesta es
    su código/definición. Match por substring sobre los nombres reales de APIs/ (así
    tolera prefijos de transporte); ante varios, gana el nombre más largo (más
    específico)."""
    q_upper = (question or "").upper()
    hits = [n for n in _list_cds_names() if "ZCDS" in n.upper() and n.upper() in q_upper]
    return max(hits, key=len) if hits else None


_CDS_TERMS = (
    "cds", "cds view", "cds views", "vista cds", "vistas cds", "extractor",
    "extractores", "api corporativa", "apis corporativas", "api de extraccion",
)


def _is_cds_inventory_query(question: str) -> bool:
    """Inventario de CDS/extractores: '¿qué CDS tenemos armadas?', '¿qué extractores
    hay?'. Distinto de un how-to ('cómo creo una CDS' → template) o de nombrar una CDS
    puntual. La respuesta es el inventario real del catálogo de APIs, no un template."""
    if _is_asset_seeking_query(question) or _is_conceptual_or_comparative_query(question):
        return False   # how-to o "template CDS" (→ asset) o comparativa no son inventario
    q = norm_text(question)
    has_cds = any(t in q for t in _CDS_TERMS)
    has_inventory = _is_availability_query(question) or any(t in q for t in _INVENTORY_TERMS)
    return has_cds and has_inventory


def _cds_inventory():
    """Inventario real de CDS views del catálogo de APIs, agrupado FACT/DIM/otras.
    None si no hay ninguna."""
    names = sorted(n for n in _list_cds_names() if "ZCDS" in n.upper())
    if not names:
        return None
    fact = [n for n in names if "_FACT_" in n.upper()]
    dim = [n for n in names if "_DIM_" in n.upper()]
    other = [n for n in names if n not in fact and n not in dim]
    return {"names": names, "fact": fact, "dim": dim, "other": other}


_CONNECTIVITY_TERMS = (
    "conexion", "conexiones", "conectividad", "conector", "conectores",
    "origenes de datos", "origen de datos", "fuentes de datos", "fuente de datos",
)
_INVENTORY_TERMS = (
    # interrogativos / listado
    "cuales", "cuantas", "cuantos", "que tipos", "listado", "lista de", "inventario",
    # verbos de cobertura / posesión (resuelto/implementado/cubierto/manejamos/usamos…)
    "resuelt", "implementad", "conectad", "cubiert", "cubrimos", "cubre", "cubrir",
    "manejamos", "usamos", "contamos", "trabajamos con", "hemos",
)


def _is_connectivity_inventory_query(question: str) -> bool:
    """Consulta de INVENTARIO de conectividad: '¿qué conexiones tenemos?', '¿qué
    orígenes de datos hay?'. Distinta de un how-to ('cómo conectar X') o de una
    conexión puntual ('configurar PostgreSQL'). Para estas, la respuesta es el
    catálogo de Arquitectura & Conectividad (la evidencia real, no una sección)."""
    if _is_howto(question) or _is_conceptual_or_comparative_query(question):
        return False   # how-to o comparativa/conceptual no son inventario
    q = norm_text(question)
    has_conn = any(t in q for t in _CONNECTIVITY_TERMS)
    has_inventory = _is_availability_query(question) or any(t in q for t in _INVENTORY_TERMS)
    return has_conn and has_inventory


def _connections_inventory(pack_name: str):
    """Resumen real del catálogo de conexiones: tipos y clientes. None si vacío."""
    try:
        _cat, cat_dir = _catalog_module()
        conns = _cat.load_architecture_connections(cat_dir)
        if not conns:
            return None
        types: dict[str, int] = {}
        clients: set[str] = set()
        for c in conns:
            label = str(getattr(c, "type_label", "") or getattr(c, "type_id", "") or "").strip()
            if label:
                types[label] = types.get(label, 0) + 1
            cli = str(getattr(c, "cliente", "") or "").strip()
            if cli:
                clients.add(cli)
        if not types:
            return None
        return {"types": sorted(types), "clients": sorted(clients)}
    except Exception:  # noqa: BLE001 — el catálogo nunca debe romper el routing
        return None


def _connectivity_catalog_action(pack_name: str) -> AssistantRouteAction | None:
    """Botón a la pestaña Catálogo del módulo 01 (Arquitectura & Conectividad), donde
    vive el catálogo de conexiones por tipo/cliente."""
    mod = next((m for m in list_modules(pack_name) if m.stem.startswith("01_")), None)
    if not mod:
        return None
    return AssistantRouteAction(
        "Ver el catálogo",
        f"/modules/{quote(mod.stem)}?pack={quote(pack_name)}&tab=catalogo",
    )


_DOMAIN_TERMS = (
    "modelo de datos", "modelos de datos", "modelo analitico", "modelos analiticos",
    "dominio", "dominios", "flujo", "flujos", "caso de uso", "casos de uso",
)


def _is_domain_inventory_query(question: str) -> bool:
    """Inventario de dominios / modelos de datos: '¿qué modelos de datos tenemos
    desarrollados?', '¿qué dominios hay?'. Distinto de un how-to o de un dominio puntual
    ('modelo de datos para despachos' → lo resuelve el lead de disponibilidad antes)."""
    if _is_asset_seeking_query(question) or _is_conceptual_or_comparative_query(question):
        return False   # how-to o comparativa/conceptual (ej. "Power BI consume modelos?")
    q = norm_text(question)
    has_domain = any(t in q for t in _DOMAIN_TERMS)
    has_inventory = _is_availability_query(question) or any(t in q for t in _INVENTORY_TERMS)
    return has_domain and has_inventory


def _domains_inventory(pack_name: str):
    """Inventario real de dominios/flujos publicados (dedup por cliente+dominio,
    ordenado por veredicto). None si vacío."""
    try:
        _cat, cat_dir = _catalog_module()
        entries = _cat.load_catalog(cat_dir)
        if not entries:
            return None
        seen, items = set(), []
        for e in entries:
            key = (e.cliente, e.dominio)
            if key in seen:
                continue
            seen.add(key)
            items.append(e)
        rank = {"plena": 0, "buena": 1, "parcial": 2}
        items.sort(key=lambda e: (rank.get(str(e.veredicto).lower(), 9), e.cliente.lower(), e.dominio.lower()))
        return items or None
    except Exception:  # noqa: BLE001 — el catálogo nunca debe romper el routing
        return None


def _domains_catalog_action(pack_name: str) -> AssistantRouteAction | None:
    """Botón a la pestaña Catálogo del módulo 04 (Modelado), donde vive el catálogo de
    dominios/flujos publicados."""
    mod = next((m for m in list_modules(pack_name) if m.stem.startswith("04_")), None)
    if not mod:
        return None
    return AssistantRouteAction(
        "Ver el catálogo de dominios",
        f"/modules/{quote(mod.stem)}?pack={quote(pack_name)}&tab=catalogo",
    )


def _find_matching_connection(question: str, pack_name: str):
    """Conexión publicada (evidencia real) cuyo OBJETO matchea la consulta, o None.
    Match fuerte: un token del objeto de la pregunta (ej. 'postgresql', 'mssql')
    coincide con la identidad de la conexión (nombre técnico / type_label / type_id /
    nombre funcional). Así "cómo configurar PostgreSQL" ofrece la conexión PostgreSQL
    que un cliente ya tiene resuelta, en vez de un how-to genérico. Lee catalog_local
    (runtime); si está vacío → None. NUNCA rompe el routing."""
    try:
        object_tokens = _query_object_tokens(question)
        if not object_tokens:
            return None
        _cat, cat_dir = _catalog_module()
        conns = _cat.load_architecture_connections(cat_dir)
        if not conns:
            return None
        for conn in conns:
            identity = norm_text(" ".join(str(part or "") for part in (
                getattr(conn, "connection", ""), getattr(conn, "type_label", ""),
                getattr(conn, "type_id", ""), getattr(conn, "business_name", ""),
            )))
            if object_tokens & set(identity.split()):
                return conn
        return None
    except Exception:  # noqa: BLE001 — el catálogo nunca debe romper el routing
        return None


# ── Filtros de catálogo por FACETA / VEREDICTO ───────────────────────────────
# Responden "¿qué conexiones usan adaptador ABAP / cloud connector / model import?"
# y "¿qué referencias reutilizables tenemos?" escaneando el catálogo REAL. Gate
# estricto (no how-to, no comparativa/conceptual, contexto de catálogo) para NO
# secuestrar consultas que ya rutean bien. Determinístico, cero tokens.

_GENERAL_EXPLAIN = (
    "El usuario consultó por una capacidad/atributo del catálogo y YA vio la respuesta "
    "basada en nuestro INVENTARIO interno; ahora eligió una respuesta GENERAL. Explicá a "
    "nivel producto SAP Datasphere / BDC: qué es la capacidad consultada y qué tipos de "
    "conexión, adaptadores o artefactos la soportan en general. NO te limites a nuestro "
    "inventario ni repitas el 'no tenemos' del catálogo — eso ya lo vio."
)

# Directiva análoga a _GENERAL_EXPLAIN pero para cuando NO hubo ningún match
# local en absoluto (route() -> None, ver AssistantRoutingService.
# no_local_evidence_explain): el usuario ya vio la respuesta grounded al
# módulo activo indicando que el contenido interno no cubre el tema, y ahora
# pidió una respuesta general. A diferencia de _GENERAL_EXPLAIN (que reancla
# a un módulo cuyo scope SÍ cubre el tema, solo que el catálogo no tenía
# inventario), acá no existe ningún módulo correcto al que anclarse -- por
# eso el caller pasa general=True a AssistantChatService.ask(), que omite
# el scope block del módulo activo en vez de confiar en esta directiva sola.
_NO_LOCAL_COVERAGE_EXPLAIN = (
    "El usuario preguntó algo que el contenido interno de Danwor no cubre — ni en "
    "el módulo activo ni en el resto del marco — y ya vio una respuesta grounded "
    "indicándolo. Ahora pidió una respuesta GENERAL: respondé con tu conocimiento "
    "general de SAP Datasphere / Business Data Cloud (BDC) sobre el tema, sin "
    "simular que es contenido curado por Danwor ni inventar una postura "
    "metodológica Danwor que no existe para este tema."
)


def _conn_feature_labels(mod, conn) -> set:
    try:
        states = mod.connection_feature_states([conn]) if hasattr(mod, "connection_feature_states") else []
        return {norm_text(str(s.get("label") or "")) for s in states if s.get("enabled")}
    except Exception:  # noqa: BLE001
        return set()


def _find_connection_facet(question: str):
    """Filtro de conexiones por faceta → (etiqueta, [labels que matchean], total) o None."""
    if _is_howto(question) or _is_conceptual_or_comparative_query(question):
        return None
    q = norm_text(question)
    if not (any(t in q for t in _CONNECTIVITY_TERMS) or "catalogo" in q):
        return None
    try:
        _cat, cat_dir = _catalog_module()
        conns = _cat.load_architecture_connections(cat_dir)
    except Exception:  # noqa: BLE001
        return None
    if not conns:
        return None
    seen, uniq = set(), []
    for c in conns:
        k = (getattr(c, "cliente", ""), getattr(c, "connection", ""), getattr(c, "type_id", ""))
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    facet = None
    if "adaptador" in q or "adapter" in q:
        for tok in ("abap", "jdbc", "odata", "mssql", "camel"):
            if tok in q:
                facet = (f"adaptador {tok.upper()}",
                         lambda c, mod, t=tok: t in norm_text(getattr(c, "adapter", "")))
                break
    if facet is None and "cloud connector" in q:
        facet = ("Cloud Connector", lambda c, mod: bool(getattr(c, "cloud_connector", False)))
    if facet is None:
        for label, keys in (("Model Import", ("model import", "importacion de modelos")),
                            ("Remote Tables", ("remote tables", "tablas remotas")),
                            ("Data Flows", ("data flows", "data flow", "flujos de datos")),
                            ("Replication Flows", ("replication flows", "replication flow", "flujos de replicacion"))):
            if any(kk in q for kk in keys):
                facet = (label, lambda c, mod, ln=norm_text(label): ln in _conn_feature_labels(mod, c))
                break
    if facet is None:
        return None
    label, pred = facet
    matches = [f"{c.cliente} · {c.connection} ({c.type_label})" for c in uniq if pred(c, _cat)]
    return (label, matches, len(uniq))


_VERDICT_QUERY = (
    (("referencia danwor", "referencias danwor", "reutilizable", "reutilizables", "plena", "plenas"), "plena"),
    (("nota de especificidad", "reserva puntual", "con reserva"), "buena"),
    (("con reservas", "no copiar", "inspiracion conceptual", "parcial", "parciales"), "parcial"),
)


def _find_flow_verdict(question: str):
    """Filtro de flujos por veredicto ("referencias reutilizables"→plena, etc.) →
    (veredicto, [entries]) o None."""
    if _is_howto(question) or _is_conceptual_or_comparative_query(question):
        return None
    q = norm_text(question)
    verdict = next((v for keys, v in _VERDICT_QUERY if any(k in q for k in keys)), None)
    if verdict is None:
        return None
    try:
        _cat, cat_dir = _catalog_module()
        entries = _cat.load_catalog(cat_dir)
    except Exception:  # noqa: BLE001
        return None
    if not entries:
        return None
    seen, matches = set(), []
    for e in entries:
        if str(getattr(e, "veredicto", "")).lower() != verdict:
            continue
        k = (e.cliente, e.dominio)
        if k not in seen:
            seen.add(k)
            matches.append(e)
    return (verdict, matches)


class AssistantRoutingService:
    """Resolve curated navigation before falling back to the LLM."""

    def route(
        self,
        document: ModuleDocument,
        question: str,
        pack_name: str,
        active_section: str = "",
    ) -> AssistantRoute | None:
        question = (question or "").strip()
        if not question:
            return None
        stem = document.summary.stem
        source = document.source
        # Skip posicional: si en el camino descartamos una recomendación por ser la
        # sección donde el usuario YA está, lo recordamos para el fallback final.
        suppressed: tuple[str, str] | None = None

        # CDS individual por nombre técnico: si la consulta nombra una CDS view
        # publicada (ej. "ZCDS_DSP_FACT_MCHB" o el artefacto "1TR_ZCDS_..."), la
        # respuesta más precisa es su código/definición del catálogo de APIs.
        cds_name = _find_matching_cds(question)
        if cds_name:
            url = f"/catalog/cds?name={quote(cds_name)}&pack={quote(pack_name)}"
            return AssistantRoute(
                f"**{cds_name}** es una CDS view del catálogo de APIs corporativas de "
                f"extracción. Acá está su definición y código.",
                (AssistantRouteAction("Ver la CDS view", url, embed=True),),
                question,
            )

        # Inventario de CDS ("¿qué CDS tenemos armadas?"): la respuesta es el catálogo
        # REAL de APIs corporativas (los extractores ya construidos), no un template.
        if _is_cds_inventory_query(question):
            inv = _cds_inventory()
            if inv:
                def _subj(name: str) -> str:
                    up = name.upper()
                    for mk in ("_FACT_", "_DIM_"):
                        i = up.find(mk)
                        if i != -1:
                            return name[i + len(mk):]
                    return name.rsplit("_", 1)[-1]
                bullets = []
                if inv["fact"]:
                    bullets.append(f"- **FACT** ({len(inv['fact'])}): {', '.join(_subj(n) for n in inv['fact'])}")
                if inv["dim"]:
                    bullets.append(f"- **DIM** ({len(inv['dim'])}): {', '.join(_subj(n) for n in inv['dim'])}")
                if inv["other"]:
                    bullets.append(f"- **Otras** ({len(inv['other'])}): {', '.join(_subj(n) for n in inv['other'])}")
                message = (
                    f"En el catálogo de **APIs corporativas de extracción** tenemos "
                    f"{len(inv['names'])} CDS views ya armadas:\n\n" + "\n".join(bullets)
                )
                order = inv["fact"] + inv["dim"] + inv["other"]
                actions = tuple(
                    AssistantRouteAction(
                        _subj(n) if n in inv["other"] else
                        f"{'FACT' if n in inv['fact'] else 'DIM'} · {_subj(n)}",
                        f"/catalog/cds?name={quote(n)}&pack={quote(pack_name)}",
                        embed=True,
                    )
                    for n in order[:12]
                )
                return AssistantRoute(message, actions, question)

        # Catálogo primero para consultas de DISPONIBILIDAD ("¿tenemos X?"): un flujo
        # REAL publicado es mejor respuesta —y evidencia— que ofrecer un template para
        # construirlo. Solo lidera si matchea a nivel dominio (umbral fuerte); si el
        # catálogo está vacío, no devuelve nada y sigue el flujo normal.
        if _is_availability_query(question) and not _is_conceptual_or_comparative_query(question):
            cat_entry = _find_matching_catalog(question, pack_name)
            if cat_entry is not None:
                label = f"{cat_entry.cliente} · {cat_entry.dominio}".strip(" ··")
                url = (f"/catalog/flow?cliente={quote(cat_entry.cliente)}"
                       f"&dominio={quote(cat_entry.dominio)}&flow={quote(cat_entry.entry_id)}"
                       f"&pack={quote(pack_name)}")
                return AssistantRoute(
                    f"Sí — ya tenemos esto implementado: **{label}** "
                    f"({cat_entry.icon} mirada Danwor {cat_entry.veredicto}). Miralo en el catálogo.",
                    (AssistantRouteAction("Ver en el catálogo", url, embed=True),),
                    question,
                )

        # Conexión como evidencia: para how-tos de configuración/conexión ("cómo
        # configurar/conectar X") —o disponibilidad— cuyo objeto YA tenemos resuelto
        # como conexión publicada, la conexión REAL lidera como referencia, en vez de
        # un how-to genérico (o, peor, un recurso tangencial por verbo compartido).
        if ((_is_asset_seeking_query(question) or _is_availability_query(question))
                and not _is_conceptual_or_comparative_query(question)):
            conn = _find_matching_connection(question, pack_name)
            if conn is not None:
                label = f"{conn.cliente} · {conn.type_label}".strip(" ··")
                url = (f"/catalog/connections/detail?cliente={quote(conn.cliente)}"
                       f"&type_id={quote(conn.type_id)}&pack={quote(pack_name)}")
                return AssistantRoute(
                    f"Referencia: **{conn.cliente}** ya tiene una conexión "
                    f"**{conn.connection}** ({conn.type_label}) resuelta en Datasphere. "
                    f"Mirá cómo está configurada en el catálogo.",
                    (AssistantRouteAction("Ver la conexión en el catálogo", url, embed=True),),
                    question,
                )

        # Inventario de conectividad ("¿qué conexiones tenemos resueltas?"): no hay un
        # objeto puntual, pero la evidencia es el catálogo de Arquitectura & Conectividad.
        # Respondemos con el inventario REAL (tipos + clientes) y deep-link al catálogo,
        # en vez de derivar a una sección genérica.
        if _is_connectivity_inventory_query(question):
            inv = _connections_inventory(pack_name)
            action = _connectivity_catalog_action(pack_name)
            if inv and action:
                clientes = ", ".join(inv["clients"])
                n_t, n_c = len(inv["types"]), len(inv["clients"])
                lead = (
                    f"En el catálogo de **Arquitectura & Conectividad** tenemos {n_t} "
                    f"tipo{'s' if n_t != 1 else ''} de conexión resuelto{'s' if n_t != 1 else ''} "
                    f"en {n_c} cliente{'s' if n_c != 1 else ''} ({clientes}):"
                )
                bullets = "\n".join(f"- **{t}**" for t in inv["types"])
                message = f"{lead}\n\n{bullets}"
                return AssistantRoute(message, (action,), question)

        # Inventario de dominios / modelos de datos ("¿qué modelos de datos / dominios
        # tenemos desarrollados?"): la evidencia es el catálogo de flujos publicados, no
        # una sección de otro módulo. Respondemos con el inventario REAL (dominios +
        # cliente + veredicto) y un botón por dominio que abre el flujo en el slot.
        if _is_domain_inventory_query(question):
            items = _domains_inventory(pack_name)
            if items:
                clientes = sorted({e.cliente for e in items})
                lead = (
                    f"En el catálogo tenemos **{len(items)} modelo"
                    f"{'s' if len(items) != 1 else ''} de datos** (dominios) desarrollado"
                    f"{'s' if len(items) != 1 else ''} en {len(clientes)} "
                    f"cliente{'s' if len(clientes) != 1 else ''}, ordenados por cumplimiento:"
                )
                bullets = "\n".join(
                    f"- {e.icon} **{e.cliente} · {e.dominio}** (mirada Danwor {e.veredicto})"
                    for e in items
                )
                message = f"{lead}\n\n{bullets}"
                actions = tuple(
                    AssistantRouteAction(
                        f"{e.cliente} · {e.dominio}",
                        (f"/catalog/flow?cliente={quote(e.cliente)}"
                         f"&dominio={quote(e.dominio)}&flow={quote(e.entry_id)}"
                         f"&pack={quote(pack_name)}"),
                        embed=True,
                    )
                    for e in items[:12]
                )
                return AssistantRoute(message, actions, question)

        # Filtro de conexiones por faceta ("¿qué conexiones usan adaptador ABAP / cloud
        # connector / model import?"): escanea el catálogo real. Sin match → afirma el
        # "no" con evidencia + "Explicame vos" (explain_module).
        facet = _find_connection_facet(question)
        if facet is not None:
            f_label, f_matches, f_total = facet
            cat_action = _connectivity_catalog_action(pack_name)
            # verbo según la faceta (adapter/cloud connector "usan"; features "tienen")
            _feat = not (f_label.lower().startswith("adaptador") or f_label == "Cloud Connector")
            v_pl, v_sg = ("tienen habilitado", "tiene habilitado") if _feat else ("usan", "usa")
            _asumo = ("Asumiendo que buscás **capital interno** (nuestro catálogo de "
                      "Arquitectura & Conectividad — orígenes y fuentes de datos)")
            # cierre tras la lista: `---` fuerza un párrafo aparte (si no, markdown lo
            # absorbe como último bullet). En el caso sin lista va inline.
            _cierre = "Si en cambio querías una respuesta general, hacé click en «Explicame vos»."
            if f_matches:
                bullets = "\n".join(f"- **{mm}**" for mm in f_matches[:12])
                acts = ((cat_action,) if cat_action else ())
                return AssistantRoute(
                    f"{_asumo}: {len(f_matches)} de las {f_total} conexiones disponibles "
                    f"{v_pl} **{f_label}**.\n\n{bullets}\n\n---\n\n{_cierre}",
                    acts, question, stem, explain_prompt=_GENERAL_EXPLAIN,
                )
            return AssistantRoute(
                f"{_asumo}: ninguna de las {f_total} conexiones disponibles {v_sg} "
                f"**{f_label}**. {_cierre}",
                (), question, stem, explain_prompt=_GENERAL_EXPLAIN,
            )

        # Filtro de flujos por veredicto ("¿qué referencias reutilizables tenemos?").
        verdict_hit = _find_flow_verdict(question)
        if verdict_hit is not None:
            v_verdict, v_matches = verdict_hit
            v_label = {"plena": "referencia Danwor reutilizable",
                       "buena": "reutilizable con nota de especificidad",
                       "parcial": "inspiración conceptual con reservas"}.get(v_verdict, v_verdict)
            if v_matches:
                bullets = "\n".join(f"- {e.icon} **{e.cliente} · {e.dominio}**" for e in v_matches)
                actions = tuple(
                    AssistantRouteAction(
                        f"{e.cliente} · {e.dominio}",
                        (f"/catalog/flow?cliente={quote(e.cliente)}"
                         f"&dominio={quote(e.dominio)}&flow={quote(e.entry_id)}"
                         f"&pack={quote(pack_name)}"),
                        embed=True,
                    )
                    for e in v_matches[:12]
                )
                _tiene = "tiene" if len(v_matches) == 1 else "tienen"
                _flujo = "flujo desarrollado" if len(v_matches) == 1 else "flujos desarrollados"
                return AssistantRoute(
                    f"Asumiendo que buscás **capital interno** (nuestro catálogo de dominios / "
                    f"modelos de datos): {len(v_matches)} {_flujo} {_tiene} mirada Danwor "
                    f"**{v_verdict}** ({v_label}).\n\n{bullets}\n\n---\n\n"
                    f"Si en cambio querías una respuesta general, hacé click en «Explicame vos».",
                    actions, question, stem, explain_prompt=_GENERAL_EXPLAIN,
                )
            return AssistantRoute(
                f"Asumiendo que buscás **capital interno** (nuestro catálogo de dominios): "
                f"por ahora ningún flujo tiene mirada Danwor **{v_verdict}** ({v_label}). "
                f"Si en cambio querías una respuesta general, hacé click en «Explicame vos».",
                (), question, stem, explain_prompt=_GENERAL_EXPLAIN,
            )

        multiple_assets = _find_matching_assets(
            question, stem, source, pack_name=pack_name,
        )
        if multiple_assets:
            actions = tuple(
                action for asset in multiple_assets
                if (action := _asset_action(asset, pack_name)) is not None
            )
            if actions:
                return AssistantRoute(
                    "Encontré varios recursos curados que pueden ayudarte. Elegí cuál querés abrir.",
                    actions,
                    question,
                )

        local_section = _find_matching_section(question, source)
        local_asset = _find_matching_asset(
            question, str(document.summary.path), source, allow_strong=True,
        )
        cross_asset = None if local_asset else _find_cross_module_asset(
            question, stem, allow_strong=True, pack_name=pack_name,
        )
        asset = local_asset or cross_asset
        cross_section = None
        # Un local match DÉBIL no debe tapar un cross-module mucho más fuerte
        # (forensic: "Configuración de Espacios"=18 por compartir con "conexión"
        # la familia de variante conect/conex de `_routing_token_variants` — le
        # ganaba SIEMPRE a "Streaming Read..."=96 en otro módulo porque el
        # cross-module ni se calculaba). `< 20` reusa el MISMO umbral que
        # `_should_pair_section_with_asset` ya usa para "score alto = señal
        # fuerte del propio matcher" (core/routing_scorers.py) — no es una
        # constante nueva. Un local FUERTE (>=20) sigue sin abrir competencia:
        # comparar siempre por score le cede la respuesta a apéndices/glosarios
        # cross-module con vocabulario amplio (ej. una pregunta de Data Product
        # perdía contra un apéndice que puntuaba 136 por eso, ver PR ticket).
        local_score = local_section.get("score", 0) if local_section else 0
        if asset and local_score < 20:
            q_low = norm_text(question)
            conn_query = any(t in q_low for t in ("odbc", "odata", "conexion", "conexión"))
            hint = _reporting_section_hint(question, pack_name)
            cross_module = _find_cross_module_section(question, stem, pack_name)
            if conn_query:
                # Protocolo/conexión: el hint ("Conexión de Herramientas Externas")
                # es el más preciso, tiene prioridad.
                cross_section = hint or cross_module
            else:
                # En el resto gana el match cross-module más fuerte; el hint de
                # reporting (Estrategia de Reporting genérica) queda de respaldo.
                # Evita que "modelo analítico" desvíe una consulta de naming.
                cross_section = cross_module or hint

        if asset:
            asset_module = str(asset.get("_module") or stem)
            asset_label = str(asset.get("description") or "recurso técnico")
            # Mismo scorer para ambos (_score_section_candidate), así que el
            # score es comparable: si los dos existen, gana el más fuerte; si
            # sólo uno existe (el caso de siempre), gana ese sin cambios.
            if local_section and cross_section:
                section = local_section if local_score >= cross_section.get("score", 0) else cross_section
            else:
                section = local_section or cross_section
            if section and not _should_pair_section_with_asset(question, section):
                section = None
            # Si la sección que lideraría es donde el usuario YA está, no la
            # re-recomendamos: la soltamos y dejamos que el recurso lidere.
            if section and _is_current_section(str(section.get("heading") or ""),
                                               str(section.get("module") or stem), stem, active_section):
                suppressed = (_display_heading(str(section.get("heading") or "")), str(section.get("heading") or ""))
                section = None
            actions: list[AssistantRouteAction] = []
            # La sección lidera (y el asset pasa a "recurso relacionado") cuando:
            #  (a) es una pregunta comparativa cuyos ejes el asset no cubre, o
            #  (b) NO hay intención operativa (how-to) y sí hay sección que matchea.
            # Así "qué es X", "X vs Y" o "cómo nombro/qué prefijo" abren la sección
            # metodológica en vez de un recurso tangencial; las how-to ("cómo
            # conecto/configuro/creo") siguen liderando con el recurso.
            has_intent = _is_asset_seeking_query(question)
            asset_is_related = (
                _is_conceptual_asset_question(question)
                and not _asset_covers_question_axes(question, asset)
            ) or (section is not None and not has_intent)
            if asset_is_related:
                is_comparative = _is_conceptual_or_comparative_query(question)
                # `is_comparative` sigue decidiendo el ROUTING (agrupa a propósito
                # comparativas y conceptuales — ambas prefieren la sección
                # metodológica sobre un how-to, ver comentario arriba) y NO se
                # toca. Este booleano es SOLO para el COPY: distingue una
                # comparación real ("diferencia", "vs", "comparar"...) de una
                # pregunta apenas conceptual ("qué es", "qué significa",
                # "explicame"), que hoy caían en el mismo "parece comparativa" y
                # sonaban a mal diagnóstico ante algo que no compara nada.
                # Reusa `_COMPARATIVE_TERMS` (arriba, antes sólo usado por
                # `_is_conceptual_asset_question`) — sin vocabulario nuevo.
                is_genuinely_comparative = any(
                    term in norm_text(question) for term in _COMPARATIVE_TERMS
                )
                if section:
                    section_module = str(section.get("module") or stem)
                    section_heading = str(section.get("heading") or "")
                    section_label = _display_heading(section_heading)
                    actions.append(AssistantRouteAction(
                        "Ir a la sección",
                        _module_url(section_module, pack_name, section_heading),
                    ))
                    explain_module = section_module
                    explain_heading = section_heading
                    if is_comparative and is_genuinely_comparative:
                        message = (
                            f"Tu pregunta parece comparativa; conviene responderla desde "
                            f"**{section_label}** antes de abrir un how-to puntual."
                        )
                    elif is_comparative:
                        message = (
                            f"Tu pregunta es conceptual; conviene responderla desde "
                            f"**{section_label}** antes de abrir un how-to puntual."
                        )
                    else:
                        message = (
                            f"Para esto conviene partir de la sección **{section_label}**."
                        )
                else:
                    explain_module = stem
                    explain_heading = ""
                    if is_genuinely_comparative:
                        message = "Tu pregunta parece comparativa; puedo explicarla en contexto metodológico."
                    else:
                        message = "Tu pregunta es conceptual; puedo explicarla en contexto metodológico."
                # El recurso "relacionado" solo se adjunta si es un match FUERTE.
                # Un asset tangencial (score bajo, ~12-15 por compartir una palabra
                # con la sección, ej. Template CDS ↔ "convención") no debe colgarse
                # de una respuesta de sección: sería ruido sin relación real.
                if asset.get("_score", 0) >= 18 and (related_action := _related_asset_action(asset, pack_name)):
                    actions.append(related_action)
                    message += f" También encontré un recurso relacionado: **{asset_label}**."
                return AssistantRoute(
                    message, tuple(actions), question, explain_module, explain_heading,
                )
            explain_module = asset_module
            explain_heading = ""
            if section:
                section_module = str(section.get("module") or stem)
                section_heading = str(section.get("heading") or "")
                actions.append(AssistantRouteAction(
                    "Ir a la sección",
                    _module_url(section_module, pack_name, section_heading),
                ))
                explain_module = section_module
                explain_heading = section_heading
            if asset_action := _asset_action(asset, pack_name):
                actions.append(asset_action)
            source_label = str(asset.get("_module_label") or "").strip()
            location = f" en **{source_label}**" if source_label else ""
            message = f"Encontré un recurso curado{location}: **{asset_label}**."
            if section:
                message += " También hay una sección metodológica relacionada."
            return AssistantRoute(
                message, tuple(actions), question, explain_module, explain_heading,
            )

        if local_section:
            heading = str(local_section.get("heading") or "")
            if slugify(_display_heading(heading)) == active_section:
                # Ya estás en esa sección → no la re-recomendamos; caé al fallback.
                suppressed = suppressed or (_display_heading(heading), heading)
            else:
                label = _display_heading(heading)
                return AssistantRoute(
                    f"Encontré una sección del módulo que puede ayudarte: **{label}**.",
                    (AssistantRouteAction("Ir a la sección", _module_url(stem, pack_name, heading)),),
                    question,
                    stem,
                    heading,
                )

        cross_section = _find_cross_module_section(question, stem, pack_name)
        if cross_section:
            target = str(cross_section.get("module") or "")
            heading = str(cross_section.get("heading") or "")
            module_label = str(cross_section.get("module_label") or target)
            return AssistantRoute(
                f"Este tema está cubierto con más precisión en **{module_label}**, "
                f"sección **{_display_heading(heading)}**.",
                (AssistantRouteAction("Ir a la sección", _module_url(target, pack_name, heading)),),
                question,
                target,
                heading,
            )

        redirect = get_out_of_scope_redirect(question, stem, pack_name)
        if redirect:
            target = _module_stem_for_number(str(redirect.get("module") or ""), pack_name)
            if target:
                return AssistantRoute(
                    str(redirect.get("message") or "Esa consulta corresponde a otro módulo."),
                    (AssistantRouteAction("Ir al módulo", _module_url(target, pack_name)),),
                    question,
                    target,
                )

        # Fallback "ya estás acá": el mejor match era la sección donde el usuario ya
        # está y no quedó nada mejor. En vez de re-recomendarla (muy básico), ofrecemos
        # otros ángulos: catálogo de dominios + "Explicame vos" (IA sobre esta sección).
        if suppressed:
            label, heading = suppressed
            actions: list[AssistantRouteAction] = []
            if (cat := _catalog_action(pack_name)):
                actions.append(cat)
            return AssistantRoute(
                f"Veo que estás en **{label}**. Si aun así el contenido no es lo que "
                f"buscabas, abordémoslo desde otro ángulo:",
                tuple(actions), question, stem, heading,
            )
        return None

    def no_local_evidence_explain(self, stem: str, active_section: str, question: str) -> AssistantRoute:
        """Affordance de "Explicame vos" para cuando route() no encontró NINGÚN
        match local (route() -> None): la respuesta grounded al módulo activo
        ya se mostró (ver webapp.routers.assistant.assistant_chat), esto sólo
        habilita pedir una respuesta GENERAL. Reusa el mismo mecanismo/forma
        (AssistantRoute.explain_module/explain_heading/explain_prompt) que ya
        consumen los filtros de catálogo con _GENERAL_EXPLAIN — no es un
        fallback paralelo — pero marca explain_general=True porque, a
        diferencia de esos filtros, no hay ningún módulo cuyo scope cubra el
        tema al que anclarse (ver AssistantChatService.ask(general=True))."""
        return AssistantRoute(
            message="",
            actions=(),
            original_question=question,
            explain_module=stem,
            explain_heading=active_section,
            explain_prompt=_NO_LOCAL_COVERAGE_EXPLAIN,
            explain_general=True,
        )
