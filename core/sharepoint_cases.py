"""
sharepoint_cases.py — lectura delegada de casos SAP desde SharePoint.

La integración usa Microsoft Graph con el access token del usuario logueado.
No persiste tokens, no escribe en SharePoint y no eleva permisos: el usuario
solo puede leer lo que Microsoft Entra/SharePoint le permitan leer.

V1: retrieval híbrido liviano por keywords normalizadas.
V2 prevista: reemplazar/acompañar search_sap_cases() con embeddings o vector store.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import logging
import hashlib
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from config import (
    SHAREPOINT_CASES_CACHE_TTL,
    SHAREPOINT_CASES_ENABLED,
    SHAREPOINT_CASES_LIST_ID,
    SHAREPOINT_CASES_MIN_SCORE,
    SHAREPOINT_CASES_SITE_ID,
    SHAREPOINT_CASES_TOP_K,
)


log = logging.getLogger(__name__)


def _ttl_cache(ttl_seconds: int) -> Callable:
    """Cache in-process con TTL — reemplaza @st.cache_data fuera de Streamlit.

    La clave es el PRIMER argumento posicional (token_fingerprint): el token real
    viaja como segundo arg con prefijo `_` y nunca entra a la clave ni se loguea.
    Expone .clear() para forzar refresco, igual que st.cache_data.
    """
    def decorator(fn: Callable) -> Callable:
        store: dict = {}
        lock = threading.Lock()

        def wrapper(*args, **kwargs):
            key = args[0] if args else None
            now = time.monotonic()
            with lock:
                hit = store.get(key)
                if hit is not None and (now - hit[0]) < ttl_seconds:
                    return hit[1]
            value = fn(*args, **kwargs)
            with lock:
                store[key] = (now, value)
            return value

        def clear() -> None:
            with lock:
                store.clear()

        wrapper.clear = clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


_STOPWORDS = {
    "a", "al", "and", "ante", "como", "con", "de", "del", "desde", "el", "en",
    "for", "hay", "la", "las", "lo", "los", "me", "of", "on", "para", "por",
    "que", "se", "si", "the", "to", "un", "una", "y",
}


@dataclass(frozen=True)
class SapCaseMatch:
    case: dict[str, Any]
    score: float
    reasons: list[str]


class _HTMLToText(HTMLParser):
    """Conversor conservador HTML -> texto plano para historias de SAP for Me."""

    _BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "div", "dl", "fieldset",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
        "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data:
            self.parts.append(data)

    def text(self) -> str:
        raw = unescape("".join(self.parts))
        raw = raw.replace("\xa0", " ")
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        raw = "\n".join(line.strip() for line in raw.splitlines())
        return raw.strip()


def _enabled() -> bool:
    return SHAREPOINT_CASES_ENABLED in {"ON", "TRUE", "1", "YES"}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {
        tok for tok in _norm(text).split()
        if len(tok) >= 3 and tok not in _STOPWORDS
    }


def _technical_terms(text: str) -> set[str]:
    """Extrae identificadores técnicos casi literales para scoring."""
    terms: set[str] = set()
    for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9_./:-]{7,}\b", text or ""):
        norm = raw.strip(".,;:()[]{}<>\"'").lower()
        if not norm:
            continue
        has_structural_char = any(ch in norm for ch in ("_", "/", ".", ":", "-"))
        has_digit = any(ch.isdigit() for ch in norm)
        has_upper_signal = raw.upper() == raw and any(ch.isalpha() for ch in raw)
        if has_structural_char or has_digit or has_upper_signal:
            terms.add(norm)
    return terms


def _best_sharepoint_search_term(query: str) -> str:
    """Termino para abrir SharePoint ya filtrado sin afectar el scoring."""
    candidates: list[str] = []
    for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9_./:-]{7,}\b", query or ""):
        term = raw.strip(".,;:()[]{}<>\"'")
        if not term:
            continue
        has_structural_char = any(ch in term for ch in ("_", "/", ".", ":", "-"))
        has_digit = any(ch.isdigit() for ch in term)
        has_upper_signal = term.upper() == term and any(ch.isalpha() for ch in term)
        if has_structural_char or has_digit or has_upper_signal:
            candidates.append(term)
    if candidates:
        return max(candidates, key=len)[:180]
    tokens = sorted(_tokens(query), key=len, reverse=True)
    return (tokens[0] if tokens else "")[:80]


def has_sharepoint_case_signal(query: str) -> bool:
    """True cuando la consulta trae un identificador tecnico casi literal."""
    return bool(_technical_terms(query))


def build_sharepoint_search_url(base_url: str, query: str) -> str:
    """Agrega el filtro de busqueda de SharePoint cuando hay un termino util."""
    term = _best_sharepoint_search_term(query)
    if not term:
        return base_url
    return _url_with_query(base_url, view="7", q=term)


def _case_list_page_url(list_url: str) -> str:
    """Vista navegable de la lista de casos SAP."""
    if not list_url:
        return ""
    parts = urllib.parse.urlsplit(list_url)
    path = parts.path.rstrip("/")
    if not path.lower().endswith(".aspx"):
        path = f"{path}/Explorar%20Casos%20SAP.aspx"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", parts.fragment))


def build_sharepoint_case_url(list_url: str, case_number: str) -> str:
    """URL estable a la lista filtrada por Numero de Caso."""
    page_url = _case_list_page_url(list_url)
    case_number = (case_number or "").strip()
    if not page_url or not case_number:
        return ""
    return _url_with_query(
        page_url,
        FilterField1="NumerodeCaso",
        FilterValue1=case_number,
        FilterType1="Text",
    )


def _url_with_query(base_url: str, **params: str) -> str:
    if not base_url:
        return ""
    parts = urllib.parse.urlsplit(base_url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v})
    return urllib.parse.urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urllib.parse.urlencode(query),
        parts.fragment,
    ))


def get_graph_headers(token: str) -> dict[str, str]:
    """Headers Graph. No loguear ni persistir el token."""
    if not token:
        raise ValueError("Token Graph vacío.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


@_ttl_cache(SHAREPOINT_CASES_CACHE_TTL)
def _fetch_sap_cases_list_url_cached(token_fingerprint: str, _token: str) -> str:
    del token_fingerprint
    site_id = urllib.parse.quote(SHAREPOINT_CASES_SITE_ID, safe=",")
    list_id = urllib.parse.quote(SHAREPOINT_CASES_LIST_ID, safe="")
    url = (
        "https://graph.microsoft.com/v1.0/"
        f"sites/{site_id}/lists/{list_id}?$select=webUrl"
    )
    import json

    req = urllib.request.Request(url, headers=get_graph_headers(_token))
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return str(payload.get("webUrl") or "")


@_ttl_cache(SHAREPOINT_CASES_CACHE_TTL)
def _fetch_sap_cases_cached(token_fingerprint: str, _token: str) -> list[dict[str, Any]]:
    # token_fingerprint fuerza cache por usuario/token sin exponer el token en logs/UI.
    del token_fingerprint
    site_id = urllib.parse.quote(SHAREPOINT_CASES_SITE_ID, safe=",")
    list_id = urllib.parse.quote(SHAREPOINT_CASES_LIST_ID, safe="")
    url = (
        "https://graph.microsoft.com/v1.0/"
        f"sites/{site_id}/lists/{list_id}/items?$expand=fields"
    )
    import json

    list_web_url = _fetch_sap_cases_list_url_cached(hashlib.sha256(_token.encode("utf-8")).hexdigest()[:16], _token)
    items: list[dict[str, Any]] = []
    while url:
        req = urllib.request.Request(url, headers=get_graph_headers(_token))
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        items.extend(payload.get("value") or [])
        url = payload.get("@odata.nextLink") or ""
    normalized = [normalize_case_item(item) for item in items if isinstance(item, dict)]
    for case in normalized:
        case["_list_web_url"] = list_web_url
    return normalized


def fetch_sap_cases(token: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Lee casos SAP desde SharePoint. Devuelve [] si está apagado o Graph falla."""
    if not _enabled() or not token:
        return []
    if force_refresh:
        _fetch_sap_cases_cached.clear()
    try:
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        cases = _fetch_sap_cases_cached(fingerprint, token)
        log.info("SharePoint SAP cases read: count=%s", len(cases))
        return cases
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            log.warning("SharePoint SAP cases unauthorized: status=%s", exc.code)
        else:
            log.warning("SharePoint SAP cases HTTP error: status=%s", exc.code)
    except Exception as exc:
        log.warning("SharePoint SAP cases unavailable: %s", type(exc).__name__)
    return []


def clean_sharepoint_html(html: str) -> str:
    """Limpia HTML largo de SharePoint/SAP for Me preservando texto útil."""
    if not html:
        return ""
    parser = _HTMLToText()
    try:
        parser.feed(str(html))
        text = parser.text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", str(html))
        text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_case_item(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields") or {}
    historia = clean_sharepoint_html(str(fields.get("Historia") or ""))
    title = str(fields.get("Title") or "").strip()
    numero = str(fields.get("NumerodeCaso") or "").strip()
    cliente = str(fields.get("Cliente") or "").strip()
    tags = str(fields.get("Tags") or "").strip()
    return {
        "title": title,
        "case_number": numero,
        "client": cliente,
        "tags": tags,
        "history": historia,
        "web_url": item.get("webUrl") or "",
        "created": item.get("createdDateTime") or "",
        "modified": item.get("lastModifiedDateTime") or "",
        "_search_text": " ".join([title, numero, cliente, tags, historia]),
    }


def _top_keywords_for_seed(text: str, limit: int = 35) -> list[str]:
    tokens = [
        tok for tok in _tokens(text)
        if not tok.isdigit() and len(tok) >= 4
    ]
    return [tok for tok, _count in Counter(tokens).most_common(limit)]


def build_cases_semantic_seed(
    cases: list[dict[str, Any]],
    *,
    history_chars: int = 1400,
    max_keywords: int = 35,
) -> list[dict[str, Any]]:
    """
    Export liviano para curar case_patterns en markdown.

    No incluye tokens ni datos de sesion. Recorta Historia para que el JSON sea
    compartible como materia prima editorial sin volcar el caso completo.
    """
    seed: list[dict[str, Any]] = []
    for case in cases or []:
        search_text = str(case.get("_search_text") or "")
        history = re.sub(r"\s+", " ", str(case.get("history") or "")).strip()
        technical_terms = sorted(_technical_terms(search_text))
        seed.append({
            "case_number": case.get("case_number") or "",
            "client": case.get("client") or "",
            "title": case.get("title") or "",
            "tags": case.get("tags") or "",
            "web_url": case.get("web_url") or "",
            "created": case.get("created") or "",
            "modified": case.get("modified") or "",
            "technical_terms": technical_terms[:30],
            "candidate_keywords": _top_keywords_for_seed(search_text, max_keywords),
            "history_preview": history[:history_chars],
            "pattern_draft": {
                "id": "",
                "label": "",
                "intent": "troubleshooting",
                "symptoms": technical_terms[:12],
                "components": [],
                "sharepoint_query": technical_terms[0] if technical_terms else "",
                "expected_behavior": "buscar casos SAP internos antes de ofrecer how-to generico o LLM",
            },
        })
    return seed


def _case_score(query_tokens: set[str], case: dict[str, Any], query_technical_terms: set[str] | None = None) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0

    fields = [
        ("title", "título", 5.0),
        ("case_number", "número de caso", 6.0),
        ("client", "cliente", 2.5),
        ("tags", "tags", 4.0),
        ("history", "historia", 1.0),
    ]
    for key, label, weight in fields:
        text_tokens = _tokens(str(case.get(key) or ""))
        hits = query_tokens & text_tokens
        if hits:
            partial = len(hits) * weight
            score += partial
            preview = ", ".join(sorted(hits)[:5])
            reasons.append(f"{label}: {preview}")

    if query_technical_terms:
        searchable_raw = str(case.get("_search_text") or "").lower()
        exact_hits = {term for term in query_technical_terms if term in searchable_raw}
        if exact_hits:
            score += 18.0 * len(exact_hits)
            reasons.append("identificador tecnico exacto: " + ", ".join(sorted(exact_hits)[:3]))

    # Boost para frases técnicas largas que suelen venir casi literales del error.
    q_norm = " ".join(sorted(query_tokens))
    searchable = _norm(str(case.get("_search_text") or ""))
    phrase_hits = 0
    for token in query_tokens:
        if len(token) >= 8 and token in searchable:
            phrase_hits += 1
    if phrase_hits >= 2:
        score += phrase_hits * 1.5
        reasons.append("coincidencias técnicas específicas en historia")

    # Penalización leve si solo hubo 1 token genérico.
    if score and len(set().union(*(query_tokens & _tokens(str(case.get(k) or "")) for k, _, _ in fields))) <= 1:
        score *= 0.45

    return score, reasons


def search_sap_cases(
    query: str,
    cases: list[dict[str, Any]],
    top_k: int = SHAREPOINT_CASES_TOP_K,
    min_score: float = SHAREPOINT_CASES_MIN_SCORE,
) -> list[SapCaseMatch]:
    """Ranking simple y explicable por campos normalizados."""
    query_tokens = _tokens(query)
    query_technical_terms = _technical_terms(query)
    sharepoint_search_term = _best_sharepoint_search_term(query)
    if not query_tokens or not cases:
        return []
    matches: list[SapCaseMatch] = []
    for case in cases:
        score, reasons = _case_score(query_tokens, case, query_technical_terms)
        if score >= min_score:
            result_case = dict(case)
            list_url = str(result_case.get("_list_web_url") or "")
            case_number = str(result_case.get("case_number") or "")
            if list_url and case_number:
                result_case["case_url"] = build_sharepoint_case_url(list_url, case_number)
            if list_url and sharepoint_search_term:
                result_case["list_search_url"] = _url_with_query(
                    _case_list_page_url(list_url),
                    view="7",
                    q=sharepoint_search_term,
                )
            matches.append(SapCaseMatch(case=result_case, score=score, reasons=reasons))
    matches.sort(key=lambda m: m.score, reverse=True)
    log.info("SharePoint SAP case matches: count=%s", len(matches[:top_k]))
    return matches[:top_k]


def format_cases_for_prompt(matches: list[SapCaseMatch]) -> str:
    """Bloque compacto para inyectar en contexto LLM sin volcar historias completas."""
    if not matches:
        return ""
    lines = [
        "CASOS SAP INTERNOS EN SHAREPOINT (lectura delegada)",
        "Usá estos casos solo como evidencia interna encontrada. No inventes casos adicionales.",
        "Si los mencionás, incluí número de caso, cliente, título, resumen breve, motivo del match y link.",
        "",
    ]
    for idx, match in enumerate(matches, start=1):
        case = match.case
        history = str(case.get("history") or "")
        summary = re.sub(r"\s+", " ", history).strip()[:900]
        reasons = "; ".join(match.reasons[:4])
        case_link = (
            case.get("case_url")
            or case.get("list_search_url")
            or case.get("web_url")
            or "N/D"
        )
        lines.extend([
            f"CASO {idx}",
            f"- Número de caso: {case.get('case_number') or 'N/D'}",
            f"- Cliente: {case.get('client') or 'N/D'}",
            f"- Título: {case.get('title') or 'N/D'}",
            f"- Tags: {case.get('tags') or 'N/D'}",
            f"- Motivo del match: {reasons or 'coincidencia textual relevante'}",
            f"- Link SharePoint: {case_link}",
            f"- Link búsqueda SharePoint: {case.get('list_search_url') or 'N/D'}",
            f"- Extracto limpio: {summary or 'N/D'}",
            "",
        ])
    return "\n".join(lines).strip()


def find_relevant_cases_for_query(
    query: str,
    token: str,
    *,
    top_k: int = SHAREPOINT_CASES_TOP_K,
    min_score: float = SHAREPOINT_CASES_MIN_SCORE,
    force_refresh: bool = False,
) -> list[SapCaseMatch]:
    cases = fetch_sap_cases(token, force_refresh=force_refresh)
    return search_sap_cases(query, cases, top_k=top_k, min_score=min_score)
