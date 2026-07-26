from __future__ import annotations

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


@dataclass(frozen=True)
class AssistantRoute:
    message: str
    actions: tuple[AssistantRouteAction, ...]
    original_question: str
    explain_module: str = ""
    explain_heading: str = ""


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


def _find_matching_cds(question: str):
    """Nombre técnico de una CDS view publicada mencionado en la consulta, o None.
    Señal muy precisa: si la pregunta nombra una CDS del catálogo de APIs (ej.
    'ZCDS_DSP_FACT_MCHB', o el artefacto con prefijo '1TR_ZCDS_...'), la respuesta es
    su código/definición. Match por substring sobre los nombres reales de APIs/ (así
    tolera prefijos de transporte); ante varios, gana el nombre más largo (más
    específico). Lee catalog_local (runtime); nunca rompe el routing."""
    try:
        import os as _os
        from config import BASE_DIR
        cat_dir = _os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        apis_dir = _os.path.join(cat_dir, "APIs")
        if not _os.path.isdir(apis_dir):
            return None
        names = [f[:-3] for f in _os.listdir(apis_dir) if f.endswith(".md")]
        q_upper = (question or "").upper()
        # candidatos cuyo nombre aparece literal en la consulta (ZCDS_ es el marcador
        # de CDS corporativa; evita falsos positivos con tokens sueltos cortos).
        hits = [n for n in names if "ZCDS" in n.upper() and n.upper() in q_upper]
        return max(hits, key=len) if hits else None
    except Exception:  # noqa: BLE001 — el catálogo nunca debe romper el routing
        return None


_CONNECTIVITY_TERMS = (
    "conexion", "conexiones", "conectividad", "origenes de datos", "origen de datos",
    "fuentes de datos", "fuente de datos",
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
    if _is_asset_seeking_query(question):   # how-to no es inventario
        return False
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
        "Ver el catálogo de conexiones",
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
                (AssistantRouteAction("Ver la CDS view", url),),
                question,
            )

        # Catálogo primero para consultas de DISPONIBILIDAD ("¿tenemos X?"): un flujo
        # REAL publicado es mejor respuesta —y evidencia— que ofrecer un template para
        # construirlo. Solo lidera si matchea a nivel dominio (umbral fuerte); si el
        # catálogo está vacío, no devuelve nada y sigue el flujo normal.
        if _is_availability_query(question):
            cat_entry = _find_matching_catalog(question, pack_name)
            if cat_entry is not None:
                label = f"{cat_entry.cliente} · {cat_entry.dominio}".strip(" ··")
                url = (f"/catalog/flow?cliente={quote(cat_entry.cliente)}"
                       f"&dominio={quote(cat_entry.dominio)}&pack={quote(pack_name)}")
                return AssistantRoute(
                    f"Sí — ya tenemos esto implementado: **{label}** "
                    f"({cat_entry.icon} mirada Danwor {cat_entry.veredicto}). Miralo en el catálogo.",
                    (AssistantRouteAction("Ver en el catálogo", url),),
                    question,
                )

        # Conexión como evidencia: para how-tos de configuración/conexión ("cómo
        # configurar/conectar X") —o disponibilidad— cuyo objeto YA tenemos resuelto
        # como conexión publicada, la conexión REAL lidera como referencia, en vez de
        # un how-to genérico (o, peor, un recurso tangencial por verbo compartido).
        if _is_asset_seeking_query(question) or _is_availability_query(question):
            conn = _find_matching_connection(question, pack_name)
            if conn is not None:
                label = f"{conn.cliente} · {conn.type_label}".strip(" ··")
                url = (f"/catalog/connections/detail?cliente={quote(conn.cliente)}"
                       f"&type_id={quote(conn.type_id)}&pack={quote(pack_name)}")
                return AssistantRoute(
                    f"Referencia: **{conn.cliente}** ya tiene una conexión "
                    f"**{conn.connection}** ({conn.type_label}) resuelta en Datasphere. "
                    f"Mirá cómo está configurada en el catálogo.",
                    (AssistantRouteAction("Ver la conexión en el catálogo", url),),
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
                types = ", ".join(f"**{t}**" for t in inv["types"])
                clientes = ", ".join(inv["clients"])
                n_t, n_c = len(inv["types"]), len(inv["clients"])
                message = (
                    f"En el catálogo de Arquitectura & Conectividad tenemos {n_t} "
                    f"tipo{'s' if n_t != 1 else ''} de conexión resuelto{'s' if n_t != 1 else ''} "
                    f"en {n_c} cliente{'s' if n_c != 1 else ''} ({clientes}): {types}. "
                    f"Abrí el catálogo para ver qué cliente usa cada uno y con qué capacidades."
                )
                return AssistantRoute(message, (action,), question)

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
        if asset and not local_section:
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
                    if is_comparative:
                        message = (
                            f"Tu pregunta parece comparativa; conviene responderla desde "
                            f"**{section_label}** antes de abrir un how-to puntual."
                        )
                    else:
                        message = (
                            f"Para esto conviene partir de la sección **{section_label}**."
                        )
                else:
                    explain_module = stem
                    explain_heading = ""
                    message = "Tu pregunta parece comparativa; puedo explicarla en contexto metodológico."
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
