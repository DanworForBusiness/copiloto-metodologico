"""
routing_scorers.py — Scorers puros del router del asistente.

Predicados y scorers de texto/contenido que deciden afinidad de una consulta con
secciones, assets y árboles. Son PUROS respecto de Streamlit: leen contenido
(cacheado vía core.corpus / core.content) pero no tocan session_state.

Extraídos verbatim de ui/tools_panel.py (Fase: mover scorers a core). La red de
caracterización en tests/ verifica que el comportamiento no cambió.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from core.utils import norm_text as _norm
from core.content_metadata import (
    split_front_matter,
    parse_section_meta,
    parse_section_excludes,
    extract_section_by_heading,
    module_label_from_md,
)
from core.content_registry import iter_content_markdown_paths
from core.corpus import get_corpus_index

log = logging.getLogger(__name__)


_ROUTING_STOPWORDS = {
    "que", "qué", "cual", "cuál", "como", "cómo", "hay", "entre", "para",
    "con", "una", "uno", "unos", "unas", "del", "las", "los", "por", "sobre",
    "esto", "este", "esta", "ese", "esa", "datasphere", "sap"
}


def _is_conceptual_or_comparative_query(question: str) -> bool:
    q = _norm(question)
    conceptual_terms = (
        "diferencia", "diferencias", "diferenciar", "comparar", "comparativa",
        "versus", " vs ", "que es", "qué es", "que significa", "qué significa",
        "explicame", "explicar", "relacion", "relación", "coexistencia",
        "hana vs", "hdlf vs", "hana db", "hana database", "hdlf"
    )
    decision_terms = (
        "que conviene", "qué conviene", "cual conviene", "cuál conviene",
        "que topologia", "qué topología", "que topologia usar", "qué topología usar",
        "recomendas", "recomendás", "decidir", "decidimos", "elegir",
        "cual uso", "cuál uso", "que uso", "qué uso"
    )
    return any(t in q for t in conceptual_terms) and not any(t in q for t in decision_terms)


def _significant_tokens(text: str) -> set[str]:
    tokens = set()
    for tok in re.findall(r"[\w/]+", _norm(text)):
        tok = tok.strip("_")
        if len(tok) < 3 or tok in _ROUTING_STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def _routing_token_variants(tokens: set[str]) -> set[str]:
    """Variantes livianas para matching editorial, no para texto final.

    Evita que diferencias simples como singular/plural o infinitivo/conjugado
    hagan invisible un asset curado: transporta <> transportar, chains <> chain.
    """
    variants = set(tokens)
    for token in tokens:
        if len(token) > 4 and token.endswith("s"):
            variants.add(token[:-1])
        if len(token) >= 8:
            variants.add(token[:8])
        # Familias operativas frecuentes en preguntas how-to. Esto permite que
        # "conexion" y "conectar" compitan como la misma intencion sin depender
        # de una lista por plataforma o caso puntual.
        if token.startswith(("conect", "conex")):
            variants.add("conect")
        if token.startswith("integr"):
            variants.add("integr")
        if token.startswith("configur"):
            variants.add("configur")
        # Familia naming: nombre/nombrar/nombro/nombres/nomenclatura -> "nombr".
        # Sin esto, "como nombro un objeto" no matchea la keyword "nombre objeto".
        if token.startswith("nombr") or token.startswith("nomencl"):
            variants.add("nombr")
    return variants


_ASSET_GENERIC_TOKEN_ROOTS = (
    "configur", "instal", "conect", "transport", "import",
    "activ", "particion", "integr"
)

_ASSET_GENERIC_TOKENS = {
    "how", "danwor", "datasphere", "template", "plantilla",
    "documentacion", "documento", "referencia", "oficial",
    "guia", "paso", "relevamiento", "tecnico", "tecnica",
    # "modelo" es palabra de dominio amplia (modelado, modelo de datos, modelo
    # analitico, modelo de seguridad...): no debe contar como token especifico
    # que rompa empates o clasifique un asset (ej. "modelo analitico" enganchaba
    # el Template de Seguridad por su keyword "modelo seguridad").
    "modelo", "modelos",
}

_ASSET_PLATFORM_ALIASES = {
    "bw": ("bw", "bw/4", "bw4", "bw bridge", "bwmt", "business warehouse"),
    "sac": ("sac", "sap analytics cloud", "analytics cloud"),
    "powerbi": ("power bi", "powerbi"),
    "s4": ("s/4", "s4", "s4hana", "s/4hana"),
    "ecc": ("ecc",),
    "aws": ("aws", "amazon"),
    "eclipse": ("eclipse", "bwmt"),
}


def _asset_platform_axes(text: str) -> set[str]:
    """Ejes tecnicos explicitos para desempatar assets por plataforma.

    No se incorporan a _significant_tokens porque tokens cortos como BW son
    ruidosos para secciones, pero en intent_assets son una senal curada fuerte:
    si el usuario pregunta por BW, un recurso SAC no deberia ganar por verbos
    compartidos como "acelerar" o "relevamiento".
    """
    norm = _norm(text or "")
    axes: set[str] = set()
    for axis, aliases in _ASSET_PLATFORM_ALIASES.items():
        for alias in aliases:
            alias_norm = _norm(alias)
            pattern = rf"(?<![\w/]){re.escape(alias_norm)}(?![\w/])"
            if re.search(pattern, norm):
                axes.add(axis)
                break
    return axes


def _asset_specific_token_hits(query_tokens: set[str], candidate_tokens: set[str]) -> set[str]:
    """Tokens que identifican el objeto real del asset, no el verbo generico.

    Ej: en "como conecto SAC con Datasphere", "conecto" y "datasphere" solo
    indican intencion/dominio; "sac" es lo que debe romper empates contra otros
    how-to de conexion.
    """
    hits = query_tokens & candidate_tokens
    return {
        token for token in hits
        if token not in _ASSET_GENERIC_TOKENS
        and not any(token.startswith(root) for root in _ASSET_GENERIC_TOKEN_ROOTS)
    }


def _query_object_tokens(question: str) -> set[str]:
    """Tokens del OBJETO de la consulta (lo que se quiere configurar/conectar/
    instalar), descartando verbos genericos (configur/instal/conect...) y palabras
    de dominio amplias (datasphere, referencia, modelo...). Ej: en "como configurar
    PostgreSQL" el objeto es {postgresql}; en "como configurar" solo es vacio.
    """
    tokens = _significant_tokens(question)
    return {
        token for token in tokens
        if token not in _ASSET_GENERIC_TOKENS
        and not any(token.startswith(root) for root in _ASSET_GENERIC_TOKEN_ROOTS)
    }


def _asset_covers_query_object(question: str, asset: dict) -> bool:
    """Guardrail de objeto. Cuando la consulta nombra un objeto especifico
    (ej. 'PostgreSQL'), un recurso curado solo es una respuesta valida si comparte
    ESE objeto — por token especifico (keywords/desc) o por eje de plataforma
    explicito (SAC, BW, PowerBI...). No alcanza con compartir el VERBO generico
    ('configurar' <-> 'configure'): eso es lo que hacia que "como configurar
    PostgreSQL" devolviera confiadamente "SAC: Configure Workspaces". Si la
    consulta no tiene objeto especifico PERO tampoco tiene NINGÚN token
    significativo (pregunta vacia/pura stopwords), el guardrail no aplica
    (devuelve True) por no haber nada que evaluar. Si en cambio la consulta
    ES contenido -- pero puramente el verbo/termino generico a secas, ej.
    "configurar" sin más -- no hay ninguna señal que justifique UN recurso
    puntual por sobre otro: el guardrail bloquea (ver forensic MTD/SLT,
    "configurar" a secas no debe resolver confiadamente ningún how-to)."""
    if not _query_object_tokens(question):
        return not _significant_tokens(question)
    if _asset_candidate_specific_hits(question, asset):
        return True
    candidate_text = " ".join([
        str(asset.get("description") or ""),
        " ".join(asset.get("keywords") or []),
    ])
    return bool(_asset_platform_axes(question) & _asset_platform_axes(candidate_text))


def _is_reference_library_module(stem: str | None, label: str | None = None) -> bool:
    text = _norm(" ".join(part for part in (stem or "", label or "") if part))
    return "biblioteca" in text and "referencia" in text


def _section_candidate_text(section_match: dict) -> str:
    return " ".join(
        str(part or "")
        for part in (
            section_match.get("heading"),
            section_match.get("module_label"),
            " ".join(section_match.get("keywords") or []),
            section_match.get("section_md"),
        )
    )


def _shared_action_roots(question: str, candidate_text: str) -> set[str]:
    q_norm = _norm(question)
    c_norm = _norm(candidate_text)
    return {
        root for root in _ASSET_GENERIC_TOKEN_ROOTS
        if root in q_norm and root in c_norm
    }


# Umbral de match "fuerte" para una sección cross-module: evidencia por sí
# sola (sin exact-keyword) suficiente para tratar el match como confiable.
# Compartido por _should_pair_section_with_asset y _section_is_concrete_enough
# para no duplicar el número mágico en dos heurísticas relacionadas.
_STRONG_SECTION_MATCH_SCORE = 20


def _should_pair_section_with_asset(question: str, section_match: dict | None) -> bool:
    """Decide si una seccion merece acompanar a un how-to concreto.

    Evita duplicidades como "Referencias de Arquitectura" + "How-to particionar
    tablas remotas": la seccion comparte vocabulario amplio, pero no la accion
    especifica. En preguntas mixtas/conceptuales, preserva la doble recomendacion.
    """
    if not section_match:
        return False
    if _is_conceptual_or_comparative_query(question):
        return True
    # Un score de seccion alto es una senal fuerte del propio matcher (keyword
    # curado exacto / mucha evidencia): en consultas SIN intencion de recurso
    # (conceptuales/naming), no dejar que la heuristica de solape de tokens la
    # descarte y deje ganar a un asset cross-module debil.
    # Ej: "como llamo al modelo analitico" -> Convencion de Nombres (72) no debe
    # ceder ante el Template de Seguridad (14). Se excluye cuando la pregunta
    # busca un recurso (how-to): ahi el asset lidera y permanece anclado a su
    # modulo, sin desviar el contexto a una seccion cross-module.
    if section_match.get("score", 0) >= _STRONG_SECTION_MATCH_SCORE and not _is_asset_seeking_query(question):
        return True

    q_norm = _norm(question)
    section_text = _norm(_section_candidate_text(section_match))
    q_tokens = _significant_tokens(question)
    section_tokens = _significant_tokens(section_text)
    specific_hits = _asset_specific_token_hits(q_tokens, section_tokens)
    action_roots = [root for root in _ASSET_GENERIC_TOKEN_ROOTS if root in q_norm]
    shares_action = any(root in section_text for root in action_roots)

    if len(specific_hits) >= 2:
        return True if not action_roots else shares_action

    # En preguntas operativas cortas, el dominio comun (Datasphere/SAP) se filtra
    # a proposito. Si queda un objeto fuerte (SAC, BW, etc.) y la seccion comparte
    # la accion solicitada, la seccion sigue siendo una buena companera del asset.
    if len(specific_hits) == 1 and action_roots and shares_action:
        return True

    return False


def _display_heading(heading: str) -> str:
    """Devuelve un heading legible para UI/chat, sin tags visuales del markdown."""
    text = heading or ""
    text = re.sub(r"\{dw-[^{}]+\}", "", text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _semantic_summary_from_fragment(fragment: str) -> str:
    m = re.search(
        r"(?ms)^\s*\{\{\s*semantic_summary\s*\}\}\s*$\n?(.*?)^\s*\{\{\s*/semantic_summary\s*\}\}\s*$",
        fragment or "",
    )
    return (m.group(1) or "").strip() if m else ""


def _has_exact_multiword_keyword(question: str, keywords: list[str] | tuple[str, ...]) -> bool:
    q_norm = _norm(question)
    for kw in keywords or []:
        kw_norm = _norm(str(kw or ""))
        if len(kw_norm.split()) > 1 and kw_norm in q_norm:
            return True
    return False


def _section_is_concrete_enough(question: str, section_match: dict) -> bool:
    """Guardrail de objeto para _find_cross_module_section.

    Mismo principio que _asset_covers_query_object (no alcanza con compartir
    el VERBO generico -- "configurar" -- con una sección amplia; hace falta
    evidencia que resuelva el OBJETO real de la consulta), pero sin su
    default permisivo para consultas sin objeto: una sección-colección
    (ej. "Colección How-To", un listado de ~20 guías distintas) puede juntar
    score por acumulación de keywords "configurar X"/"instalar Y" sin que
    NINGUNA cubra lo preguntado. Ahí es exactamente donde hace falta negar
    el match por default, no asumirlo válido.

    Evidencia aceptada -- deliberadamente SIN gate de score: una sección
    puntualmente correcta puede rankear apenas debajo de
    _STRONG_SECTION_MATCH_SCORE (ej. "Artefactos y uso Semántico"=19 para
    "capa de consumption") y no por eso es menos concreta; lo que importa es
    si el objeto REAL de la consulta aparece en el contenido propio de la
    sección, no el acumulado de señales difusas que compone el score:
    - una keyword curada MULTI-PALABRA exacta (señal fuerte, igual que el
      desempate ya usado en _find_cross_module_section), o
    - el objeto especifico de la consulta (o un eje de plataforma explícito
      -- SAC, BW, PowerBI...) aparece en el propio texto de la sección
      (heading/keywords/cuerpo). Sin objeto especifico en la consulta, no
      hay nada que verificar -- se rechaza (ver "configurar" a secas).
    """
    keywords = section_match.get("keywords") or []
    if _has_exact_multiword_keyword(question, keywords):
        return True
    query_objects = _query_object_tokens(question)
    if not query_objects:
        return False
    candidate_text = _section_candidate_text(section_match)
    if query_objects & _significant_tokens(candidate_text):
        return True
    return bool(_asset_platform_axes(question) & _asset_platform_axes(candidate_text))


def _score_section_candidate(question: str, heading: str, keywords: list[str], section_md: str, module_scope: str = "") -> int:
    q_norm = _norm(question)
    q_tokens = _significant_tokens(question)
    q_variants = _routing_token_variants(q_tokens)
    heading_tokens = _significant_tokens(_display_heading(heading))
    heading_variants = _routing_token_variants(heading_tokens)
    summary = _semantic_summary_from_fragment(section_md)
    summary_tokens = _significant_tokens(summary)
    summary_variants = _routing_token_variants(summary_tokens)
    scope_tokens = _significant_tokens(module_scope)

    score = 0
    for kw in keywords or []:
        kw_norm = _norm(kw)
        if not kw_norm:
            continue
        if kw_norm in q_norm:
            # Un keyword curado multi-palabra exacto debe ganar sobre
            # acumulaciones difusas de tokens compartidos en secciones amplias.
            score += 36 if len(kw_norm.split()) > 1 else 8
            continue
        kw_tokens = _significant_tokens(kw)
        kw_variants = _routing_token_variants(kw_tokens)
        hits = len(q_variants & kw_variants)
        if hits >= 2:
            score += hits * 3
            if kw_tokens and kw_tokens <= q_variants:
                score += 4

    heading_hits = len(q_variants & heading_variants)
    if heading_hits >= 2:
        score += heading_hits * 2

    summary_hits = len(q_variants & summary_variants)
    if summary_hits >= 2:
        score += summary_hits * 2

    # Module scope is a tie-breaker only; broad module vocabulary should not
    # route a question by itself.
    if score > 0:
        scope_hits = len(q_tokens & scope_tokens)
        if scope_hits >= 2:
            score += min(scope_hits, 4)

        candidate_action_text = _norm(" ".join([
            _display_heading(heading),
            " ".join(keywords or []),
            summary,
        ]))
        if any(root in q_norm and root in candidate_action_text for root in _ASSET_GENERIC_TOKEN_ROOTS):
            score += 6

    # Boost de intencion de naming: una consulta que pregunta COMO NOMBRAR algo
    # ("como nombro/llamo un X", "convencion/nomenclatura/prefijo/sufijo de X")
    # debe resolverse desde la seccion de Convencion de Nombres, sin importar el
    # objeto X. Sin esto, "como nombro un replication flow" se iba a "Tipos de
    # Vistas SQL" porque el objeto pesa mas que la intencion. Escalable: no
    # depende de enumerar cada artefacto en las keywords.
    naming_intent = any(t in q_norm for t in (
        "como nombro", "como nombrar", "como llamo", "como se llama", "como nombramos",
        "convencion", "convención", "nomenclatura", "prefijo", "sufijo", "naming",
    ))
    heading_norm = _norm(_display_heading(heading))
    naming_section = (
        any(t in heading_norm for t in ("convencion", "convención", "nomenclatura", "nombres"))
        or any(("nombr" in _norm(k) or "nomencl" in _norm(k) or "prefijo" in _norm(k) or "sufijo" in _norm(k))
               for k in (keywords or []))
    )
    if naming_intent and naming_section:
        score += 40

    return score


def _find_cross_module_section(
    question: str, current_stem: str, pack_name: str | None = None,
) -> dict | None:
    """
    Busca una seccion relevante fuera del modulo activo sin abrir todo el corpus.

    Solo mira metadata editorial: frontmatter scope, section_meta,
    semantic_summary y headings. Si el match no es claro, deja que siga el
    fallback LLM actual.
    """
    candidates: list[tuple[int, dict]] = []

    # Corpus parseado y cacheado por mtime (core.corpus). El scoring por pregunta
    # sigue acá; solo el parseo dejó de repetirse en cada prompt.
    modules = sorted(get_corpus_index(pack_name), key=lambda m: Path(m.path).name.lower())
    for module in modules:
        if module.stem == current_stem:
            continue
        for section in module.sections:
            heading = section.heading
            if not heading:
                continue
            section_md = section.section_md
            score = _score_section_candidate(
                question,
                heading,
                list(section.keywords),
                section_md,
                module_scope=module.scope,
            )
            if score >= 12:
                candidates.append((
                    score,
                    {
                        "module": module.stem,
                        "module_label": module.module_label,
                        "path": section.path,
                        "heading": heading,
                        "section_md": section_md,
                        "keywords": list(section.keywords),
                        "score": score,
                    },
                ))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    if second_score and best_score - second_score < 4:
        best_exact = _has_exact_multiword_keyword(question, best.get("keywords") or [])
        second_exact = _has_exact_multiword_keyword(question, candidates[1][1].get("keywords") or [])
        if not (best_exact and not second_exact):
            return None
    if not _section_is_concrete_enough(question, best):
        return None
    return best


# ── Intent Section Detection ──────────────────────────────────────────────────
def _find_matching_section(question: str, context: str) -> dict | None:
    """
    Busca una sección del módulo activo relevante para la pregunta.

    Nivel 1 — ACTIVO: {{section_meta: keywords="..."}} en el .md
      Matching explícito por keywords declaradas en el contenido.
      Máxima precisión — fuente de verdad para el routing de secciones.

    Nivel 2 — DESHABILITADO (plug-and-play, ver bloque comentado al final)
      Matching por texto libre sobre el cuerpo de cada sección.
      Deshabilitado porque en dominios técnicos con vocabulario denso
      (ej: "conexion", "abap", "datos" aparecen en casi todas las secciones)
      genera falsos positivos frecuentes que degradan la experiencia.
      Para rehabilitar: descomentar el bloque al final de esta función
      y ajustar MIN_SECTION_WORDS en config.py (valor sugerido: 4+).
    """
    try:
        from core.content_metadata import (
            parse_section_meta, parse_section_excludes,
            split_front_matter,
        )
        _, body = split_front_matter(context)
    except Exception:
        return None

    q_norm = _norm(question)

    try:
        section_excludes = parse_section_excludes(body)
    except Exception:
        section_excludes = {}

    def _is_excluded(heading: str) -> bool:
        excl = [_norm(k) for k in section_excludes.get(heading, [])]
        return bool(excl) and any(ex in q_norm for ex in excl)

    # ── Nivel 1: section_meta explícito ──────────────────────────────────────
    try:
        sections = parse_section_meta(body)
        scored_sections: list[tuple[int, dict, bool]] = []
        for section in sections:
            heading = section.get("heading", "")
            if _is_excluded(heading):
                continue
            section_md = extract_section_by_heading(body, heading, level=(2, 3)) or ""
            score = _score_section_candidate(
                question,
                heading,
                section.get("keywords") or [],
                section_md,
            )
            # Evita que una seccion local gane por vocabulario compartido
            # demasiado generico (ej: datos, espacios) y bloquee un match
            # transversal mas preciso. Un score 8 equivale, como minimo, a un
            # keyword simple explicito o a suficiente evidencia semantica.
            if score >= 8:
                action_text = " ".join([heading, " ".join(section.get("keywords") or [])])
                has_action_root = bool(_shared_action_roots(question, action_text))
                section = dict(section)
                section["score"] = score
                scored_sections.append((score, section, has_action_root))
        if scored_sections:
            action_scored = [item for item in scored_sections if item[2]]
            candidates = action_scored or scored_sections
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
    except Exception:
        pass

    # ── Nivel 2: texto libre — DESHABILITADO ─────────────────────────────────
    # Para rehabilitar: descomentar el bloque siguiente y ajustar
    # MIN_SECTION_WORDS en config.py (actualmente = 3, sugerido: 4+)
    #
    # from config import MIN_SECTION_WORDS as MIN_WORDS, SECTION_STOPWORDS as DOMAIN_STOPWORDS
    # from core.content import parse_markdown_headings, strip_section_meta
    # try:
    #     clean_body = strip_section_meta(body)
    #     headings   = parse_markdown_headings(clean_body)
    #     q_words = [re.sub(r'[^\w]', '', w) for w in q_norm.split() if len(w) > 3]
    #     q_words = [w for w in q_words if len(w) > 3 and w not in DOMAIN_STOPWORDS]
    #     if not q_words:
    #         return None
    #     for i, (lvl, title, hpos) in enumerate(headings):
    #         if _is_excluded(title):
    #             continue
    #         next_pos     = headings[i + 1][2] if i + 1 < len(headings) else len(clean_body)
    #         section_text = _norm(clean_body[hpos:next_pos])
    #         hits         = sum(1 for w in q_words if w in section_text)
    #         if hits >= MIN_WORDS:
    #             return {"heading": title, "keywords": q_words}
    # except Exception:
    #     pass

    return None


# ── Intent Asset Detection ────────────────────────────────────────────────────
_ASSET_INTENT_TERMS = (
    "template", "plantilla", "codigo", "código", "ejemplo", "ver recurso",
    "ver template", "mostrar template", "descargar", "copiar", "crear cds",
    "escribir cds", "implementacion", "implementación", "documentacion",
    "documentación", "link", "enlace", "sap help", "nota sap", "kba",
    "how-to", "guia", "guía", "paso a paso", "configurar", "configuro",
    "configuración", "configuracion", "instalar", "conectar", "transportar",
    "transporta", "transporte", "transportes",
    "activar", "fuente oficial", "documento", "referencia",
    "como integro", "cómo integro", "como integrar", "cómo integrar",
    "integrar process"
)


def _is_asset_seeking_query(question: str) -> bool:
    q_lower = _norm(question)
    if any(term in q_lower for term in _ASSET_INTENT_TERMS):
        return True
    # Raices operativas comunes. Evita depender de cada conjugacion:
    # conectar/conecto/conectamos, transportar/transporta, etc.
    return any(
        root in q_lower
        for root in ("configur", "instal", "conect", "transport", "import", "activ", "particion", "integr")
    )


_ASSET_LISTING_TERMS = (
    "que documentacion", "que documentación", "documentacion tenemos", "documentación tenemos",
    "que documentos", "que recursos", "que links", "que enlaces", "que referencias",
    "que material", "que tenemos", "tenemos documentacion", "tenemos documentación",
    "tenemos recursos", "tenemos links", "tenemos referencias", "documentacion sobre",
    "documentación sobre", "recursos sobre", "links sobre", "referencias sobre",
    "material sobre", "biblioteca sobre"
)


def _is_asset_listing_query(question: str) -> bool:
    q_lower = _norm(question)
    return any(term in q_lower for term in _ASSET_LISTING_TERMS)


def _has_naming_intent(question: str) -> bool:
    q = _norm(question)
    return any(term in q for term in (
        "como nombro", "como nombrar", "como llamo", "como se llama",
        "como nombramos", "convencion", "nomenclatura",
        "prefijo", "sufijo", "naming",
    ))


def _is_naming_asset(asset: dict) -> bool:
    text = _norm(" ".join(str(part or "") for part in (
        asset.get("description"),
        " ".join(asset.get("keywords") or []),
        asset.get("asset"),
        asset.get("url"),
    )))
    return any(term in text for term in (
        "convencion", "nomenclatura", "nombre", "nombres",
        "nombr", "prefijo", "sufijo", "naming",
    ))


def _has_troubleshooting_or_reference_intent(question: str) -> bool:
    q = _norm(question)
    terms = (
        "error", "errores", "fallo", "falla", "failed", "exception",
        "problema", "issue", "issues", "troubleshooting", "debug", "depurar",
        "trace", "traza", "st05", "su53", "slg1", "odqmon", "sddlar",
        "dhcdcmon", "dhrdbmon", "oom", "out of memory", "memory",
        "not ready", "data_not_ready", "nota", "notas", "kba", "caso",
        "casos", "sap help", "documentacion", "referencia",
        "referencias", "recurso", "recursos", "help", "how-to", "how to",
    )
    return any(term in q for term in terms)


def _is_troubleshooting_asset(asset: dict) -> bool:
    text = _norm(" ".join(str(part or "") for part in (
        asset.get("description"),
        " ".join(asset.get("keywords") or []),
        asset.get("asset"),
        asset.get("url"),
    )))
    terms = (
        "troubleshooting", "error", "errores", "issue", "issues",
        "data_not_ready", "not ready", "failed", "fallo", "falla",
        "out of memory", "oom", "trace", "traza", "st05", "su53",
        "slg1", "dhcdcmon", "dhrdbmon", "odqmon", "sddlar",
        "replication flow issues", "problemas de flujo", "atascada",
        "atascado", "debug", "depurar",
    )
    return any(term in text for term in terms)


def _should_skip_asset_for_question(question: str, asset: dict) -> bool:
    # En preguntas de naming, un asset solo debe competir si tambien trata
    # convenciones/nomenclatura. Evita que un recurso operativo gane solo por
    # compartir el artefacto (ej. Replication Flow) cuando la pregunta es como
    # nombrarlo.
    if _has_naming_intent(question) and not _is_naming_asset(asset):
        return True

    # Los assets de troubleshooting solo compiten cuando el usuario pide errores,
    # casos, notas, documentacion o una guia operativa concreta. Antes solo se
    # excluian en preguntas conceptuales; se amplia a CUALQUIER pregunta sin
    # intencion de troubleshooting/referencia (ej. "como expongo datos para
    # consumo" no debe resolverse con una nota DATA_NOT_READY).
    return (
        not _has_troubleshooting_or_reference_intent(question)
        and _is_troubleshooting_asset(asset)
    )


def _score_asset_candidate(question: str, asset: dict) -> int:
    q_lower = _norm(question)
    q_tokens = _significant_tokens(question)
    q_tokens_loose = _routing_token_variants(q_tokens)
    keywords = [_norm(k) for k in (asset.get("keywords") or [])]
    desc = _norm(str(asset.get("description") or ""))
    score = 0
    query_axes = _asset_platform_axes(question)
    candidate_axes = _asset_platform_axes(" ".join([desc, " ".join(keywords)]))
    matching_axes = query_axes & candidate_axes
    conflicting_axes = query_axes and candidate_axes and not matching_axes
    if matching_axes:
        score += 18 * len(matching_axes)
    if conflicting_axes:
        score -= 18

    datasphere_domain_counted = False
    for kw in keywords:
        if not kw:
            continue
        if kw in q_lower:
            # Un keyword curado multi-palabra exacto representa intenci?n
            # operativa precisa (ej: conectar SAC y Datasphere) y debe ganar
            # sobre matches amplios de plataforma o dominio.
            if len(kw.split()) > 1:
                score += 36
            elif kw in _ASSET_GENERIC_TOKENS or any(kw.startswith(r) for r in _ASSET_GENERIC_TOKEN_ROOTS):
                # Keyword generico de una sola palabra (datasphere, referencia,
                # documentacion, configurar...): senal debil de dominio, no debe
                # clasificar un asset por si solo ni preemptar una seccion precisa.
                # Sin esto, el SAP Help generico de la biblioteca gana cualquier
                # pregunta que mencione "datasphere".
                score += 2
            else:
                score += 12
            continue
        if "bw bridge" in kw and "bw bridge" in q_lower:
            score += 6
        for root in ("configur", "instal", "conect", "transport", "import", "activ", "particion"):
            if root in kw and root in q_lower:
                score += 6
                break
        # Dominio compartido (datasphere): nudge chico y UNA sola vez por asset.
        # Antes sumaba +4 por cada keyword con "datasphere", inflando recursos
        # genericos con muchas keywords de dominio (ej. SAP Help oficial → +20).
        if "datasphere" in kw and "datasphere" in q_lower and not datasphere_domain_counted:
            score += 4
            datasphere_domain_counted = True
        kw_tokens = _significant_tokens(kw)
        kw_tokens_loose = _routing_token_variants(kw_tokens)
        specific_hits = _asset_specific_token_hits(q_tokens, kw_tokens)
        if specific_hits:
            # Premia el objeto consultado (SAC, PowerBI, BW Bridge, process/task
            # chain, etc.) por encima de verbos y dominios compartidos.
            score += 8 * len(specific_hits)
        hits = len(q_tokens_loose & kw_tokens_loose)
        if hits >= 2:
            score += hits * 3
            if hits == len(kw_tokens_loose):
                score += 4
        elif hits == 1:
            # Un token especifico del objeto consultado (SAC, OpenID, BWMT,
            # PowerBI, etc.) debe pesar mas que el verbo generico "conectar".
            # Solo tokens ESPECIFICOS (excluye dominio "datasphere" y genericos)
            # para que el solapamiento de dominio no otorgue este bonus.
            if _asset_specific_token_hits(q_tokens, kw_tokens):
                score += 6

    desc_tokens = _significant_tokens(desc)
    desc_tokens_loose = _routing_token_variants(desc_tokens)
    desc_specific_hits = _asset_specific_token_hits(q_tokens, desc_tokens)
    if desc_specific_hits:
        score += 4 * len(desc_specific_hits)
    desc_hits = len(q_tokens_loose & desc_tokens_loose)
    if desc_hits >= 2:
        score += desc_hits
    elif desc_hits == 1 and (q_tokens & desc_tokens):
        score += 3
    for root in ("configur", "instal", "conect", "transport", "import", "activ", "particion"):
        if root in desc and root in q_lower:
            score += 3
            break
    if "datasphere" in desc and "datasphere" in q_lower:
        score += 2
    if "bw bridge" in desc and "bw bridge" in q_lower:
        score += 3
    return score


def _asset_candidate_specific_hits(question: str, asset: dict) -> set[str]:
    q_tokens = _significant_tokens(question)
    candidate_text = " ".join(
        str(part or "")
        for part in (
            asset.get("description"),
            " ".join(asset.get("keywords") or []),
            asset.get("language"),
        )
    )
    return _asset_specific_token_hits(q_tokens, _significant_tokens(candidate_text))


def _dedupe_assets(scored: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    seen: set[str] = set()
    deduped: list[tuple[int, dict]] = []
    for score, asset in scored:
        key = _norm(str(asset.get("asset") or asset.get("description") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((score, asset))
    return deduped


def _find_matching_assets(
    question: str,
    current_stem: str,
    context: str,
    *,
    limit: int = 5,
    pack_name: str | None = None,
) -> list[dict]:
    """Devuelve varios recursos cuando el usuario pide explorar documentacion.

    Para preguntas operativas/directas conservamos el flujo de un único asset.
    Esta rama cubre consultas tipo "que documentacion tenemos de X", donde la
    respuesta correcta puede ser un abanico de guias, notas y referencias.
    """
    if not _is_asset_listing_query(question):
        return []

    scored: list[tuple[int, dict]] = []

    def _add_assets(assets: list[dict], module_stem: str, module_label: str | None) -> None:
        for raw_asset in assets or []:
            score = _score_asset_candidate(question, raw_asset)
            if score < 8:
                continue
            # Exigimos al menos un token de tema real. Esto evita que "documentacion"
            # por si solo arrastre recursos genericos sin relacion con el objeto.
            specific_hits = _asset_candidate_specific_hits(question, raw_asset)
            if not specific_hits:
                continue
            asset = dict(raw_asset)
            asset["_module"] = module_stem
            asset["_module_label"] = module_label
            asset["_specific_hits"] = sorted(specific_hits)
            scored.append((score, asset))

    try:
        meta, _body = split_front_matter(context)
        _add_assets(meta.get("intent_assets") or [], current_stem, module_label_from_md(current_stem, context))
    except Exception:
        pass

    content_paths = iter_content_markdown_paths(pack_name)

    for path in sorted(content_paths, key=lambda p: p.name.lower()):
        if path.stem == current_stem:
            continue
        try:
            md = path.read_text(encoding="utf-8", errors="ignore")
            meta, _body = split_front_matter(md)
            module_label = module_label_from_md(path.stem, md)
        except Exception as exc:
            log.warning("Asset listing: no se pudo leer %s, se omite: %s", path.name, exc)
            continue
        _add_assets(meta.get("intent_assets") or [], path.stem, module_label)

    scored = _dedupe_assets(scored)
    if len(scored) < 2:
        return []
    scored.sort(key=lambda item: (-item[0], str(item[1].get("description") or "")))
    best_score = scored[0][0]
    best_hits = set(scored[0][1].get("_specific_hits") or [])
    selected = []
    for score, asset in scored:
        asset_hits = set(asset.get("_specific_hits") or [])
        same_topic = bool(best_hits and asset_hits and (best_hits & asset_hits))
        if same_topic or score >= max(8, best_score - 12):
            selected.append(asset)
        if len(selected) >= limit:
            break
    return selected if len(selected) >= 2 else []


def _find_best_asset(
    question: str,
    assets: list[dict],
    *,
    require_intent: bool = True,
    min_score: int = 8,
    min_gap: int = 3,
) -> dict | None:
    if require_intent and not _is_asset_seeking_query(question):
        return None
    scored: list[tuple[int, dict]] = []
    for asset in assets or []:
        score = _score_asset_candidate(question, asset)
        if score >= min_score:
            scored.append((score, asset))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if second_score and best_score - second_score < min_gap:
        return None
    # Guardrail de objeto: no devolver un recurso que solo comparte el verbo
    # generico con la consulta (ej. "configurar PostgreSQL" -> SAC Workspaces).
    if not _asset_covers_query_object(question, best):
        return None
    return {**best, "_score": best_score}


def _find_matching_asset(
    question: str,
    selected_path: str,
    context: str,
    *,
    allow_strong: bool = False,
) -> dict | None:
    try:
        from core.content_metadata import split_front_matter
        meta, _ = split_front_matter(context)
        assets = meta.get("intent_assets") or []
    except Exception:
        return None

    q_lower = _norm(question)
    conceptual_terms = (
        "diferencia", "comparar", "comparativa", "versus", " vs ", "mejor",
        "funciona mejor", "recomendas", "recomendás", "conviene", "por que",
        "por qué", "explicame", "explicar", "corrige", "corregir", "malinformando",
        "ddic", "view entity", "entity", "define view entity", "define view"
    )
    has_intent = _is_asset_seeking_query(question)
    if any(term in q_lower for term in conceptual_terms) and not (has_intent or allow_strong):
        return None
    filtered_assets = [
        asset for asset in assets
        if not _should_skip_asset_for_question(question, asset)
    ]
    return _find_best_asset(
        question,
        filtered_assets,
        require_intent=not allow_strong,
        min_score=8 if has_intent else 12,
        min_gap=3 if has_intent else 4,
    )


def _find_cross_module_asset(
    question: str,
    current_stem: str,
    *,
    allow_strong: bool = False,
    pack_name: str | None = None,
) -> dict | None:
    has_intent = _is_asset_seeking_query(question)
    if not has_intent and not allow_strong:
        return None
    # Una palabra suelta y ambigua (ej. "modelo", "datos") sin intencion operativa
    # es demasiado debil para arrastrar un recurso de OTRO modulo: derivaria en
    # asociaciones cruzadas erroneas (modelo -> template de seguridad). Que lo
    # resuelva una seccion local o el LLM.
    if not has_intent and len(_significant_tokens(question)) < 2:
        return None
    min_score = 8 if has_intent else 12
    min_gap = 3 if has_intent else 4
    candidates: list[tuple[int, dict]] = []
    content_paths = iter_content_markdown_paths(pack_name)

    for path in sorted(content_paths, key=lambda p: p.name.lower()):
        if path.stem == current_stem:
            continue
        try:
            # Los intent_assets suelen ajustarse durante la curaduria del marco.
            # Para routing conversacional preferimos leerlos frescos y evitar que
            # un cache de Streamlit deje invisible una nota/referencia recien agregada.
            md = path.read_text(encoding="utf-8", errors="ignore")
            meta, _body = split_front_matter(md)
            module_label = module_label_from_md(path.stem, md)
        except Exception as exc:
            log.warning("Cross-module asset: no se pudo leer %s, se omite: %s", path.name, exc)
            continue
        for asset in meta.get("intent_assets") or []:
            if _should_skip_asset_for_question(question, asset):
                continue
            score = _score_asset_candidate(question, asset)
            if score >= min_score:
                asset = dict(asset)
                asset["_module"] = path.stem
                asset["_module_label"] = module_label
                candidates.append((score, asset))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    if second_score and best_score - second_score < min_gap:
        return None
    # Guardrail de objeto: idem _find_best_asset, tambien cross-module (el SAC
    # "Configure Workspaces" de A04 se colaba desde el modulo 01 por el verbo).
    if not _asset_covers_query_object(question, best):
        return None
    best["_score"] = best_score
    return best


def _needs_cds_delta_choice(question: str) -> bool:
    q = _norm(question)
    cds_terms = ("cds", "odp", "abap_cds", "datasphere")
    delta_terms = (
        "delta", "incremental", "incrementalidad", "puntero", "cdc",
        "change data capture", "cambios", "actualizados", "actualizacion",
        "actualización"
    )
    already_specific = (
        "byelement", "by element", "change data capture", "mapping",
        "join", "joins", "tabla principal", "role main", "release",
        "version", "versión", "s4hana 202", "abap 7", "rap"
    )
    asks_how = (
        "como", "cómo", "incluir", "agregar", "configurar", "implementar",
        "soporte", "soporta", "me conviene", "recomendas", "recomendás"
    )
    return (
        any(term in q for term in cds_terms)
        and any(term in q for term in delta_terms)
        and any(term in q for term in asks_how)
        and not any(term in q for term in already_specific)
    )


# ── Tree intent detection ─────────────────────────────────────────────────────
def _find_matching_tree(question: str, trees: list) -> object | None:
    """
    Nivel A: scoring local con filtros exclude_if y min_sources.

    Los arboles se ofrecen solo cuando la metadata local alcanza. No usamos
    LLM arbitro como fallback porque ante preguntas operativas/how-to tiende a
    fabricar afinidad con arboles amplios (ej. Topologia de Espacios).
    """
    if not trees:
        return None

    q_norm = _norm(question)
    if _is_conceptual_or_comparative_query(question):
        # Los árboles resuelven decisiones. Para preguntas conceptuales o
        # comparativas, una sección/LLM debe ganar salvo que el usuario pida
        # explícitamente decidir, elegir o recomendar una topología.
        return None
    scored = []

    for tree in trees:
        title = _norm(tree.get("title", "") or "")
        tags  = [_norm(t) for t in (tree.tags if hasattr(tree, "tags") else tree.get("tags", []) or [])]

        raw_spec   = getattr(tree, "source_path", "") or tree.get("_path", "") or ""
        keywords:   list[str] = []
        exclude_if: list[str] = []
        try:
            from pathlib import Path as _Path
            if raw_spec:
                _spec      = yaml.safe_load(_Path(raw_spec).read_text(encoding="utf-8"))
                keywords   = [_norm(k) for k in (_spec.get("keywords")   or [])]
                exclude_if = [_norm(k) for k in (_spec.get("exclude_if") or [])]
        except Exception as exc:
            log.warning("Tree spec ilegible (%s), se ignora keywords/exclude_if: %s", raw_spec, exc)

        if any(ex in q_norm for ex in exclude_if):
            continue

        sources_hit = set()
        score = 0

        for kw in keywords:
            if kw in q_norm:
                score += 3
                sources_hit.add("keywords")
        for tag in tags:
            if tag in q_norm:
                score += 2
                sources_hit.add("tags")
        for word in title.split():
            if len(word) > 3 and word in q_norm:
                score += 1
                sources_hit.add("title")

        if score > 0 and len(sources_hit) >= 2:
            scored.append((score, tree))

    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    return None
