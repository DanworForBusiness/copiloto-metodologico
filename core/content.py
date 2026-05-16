from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from core.state import AppState
from core.utils import norm_text as _norm
from config import SECTION_NUMBER_WIDTH

import html
import json
import re
import streamlit as st
import yaml

_FONT_SIZE_KEY = "_content_font_size"
_FONT_MIN = 12
_FONT_MAX = 18
_FONT_DEFAULT = 14


# =========================================================
# Data model + lectura
# =========================================================

@dataclass(frozen=True)
class ModuleDoc:
    stem: str
    path: Path
    meta: dict
    body: str


@st.cache_data(show_spinner=False)
def read_text_cached(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8", errors="ignore")


def split_front_matter(md: str) -> tuple[dict, str]:
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) >= 3:
            raw = parts[1].strip()
            body = parts[2].lstrip("\n")
            try:
                meta = yaml.safe_load(raw) or {}
            except Exception:
                meta = {}
            return meta, body
    return {}, md


def parse_markdown_headings(md: str):
    out = []
    for m in re.finditer(r"^(#{1,6})\s+(.+)$", md, flags=re.M):
        lvl = len(m.group(1))
        title = m.group(2).strip()
        out.append((lvl, title, m.start()))
    return out


def _heading_line_index_at(md: str, start_pos: int) -> int:
    """Devuelve el indice de linea para una posicion de heading en el markdown."""
    return md.count("\n", 0, start_pos)


def _section_id_after_heading(lines: list[str], heading_line_idx: int) -> str:
    """Lee {{section_id: NN}} inmediatamente debajo de un heading, si existe."""
    i = heading_line_idx + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return ""
    match = SECTION_ID_RE.match(lines[i].strip())
    return (match.group(1) or "").strip() if match else ""


def parse_section_meta(md_body: str) -> list[dict]:
    """
    Extrae los metadatos de intent detection de secciones del .md.
    Lee tags {{section_meta: keywords="..."}} y los asocia al heading
    más cercano que los precede.

    Retorna lista de dicts: [{"heading": str, "keywords": [str, ...]}, ...]

    El tag es invisible al renderizado — strip_section_meta() lo elimina
    antes de que el contenido se muestre al usuario.
    """
    headings = parse_markdown_headings(md_body)
    results  = []

    for match in SECTION_META_RE.finditer(md_body):
        kv_match = SECTION_META_KV_RE.search(match.group(1))
        if not kv_match:
            continue
        raw_kws  = kv_match.group(1)
        keywords = [k.strip() for k in raw_kws.split(",") if k.strip()]

        # Asociar al heading inmediatamente anterior al tag
        tag_pos = match.start()
        heading = None
        for lvl, title, hpos in reversed(headings):
            if hpos < tag_pos:
                heading = title
                break

        if heading and keywords:
            results.append({"heading": heading, "keywords": keywords})

    return results


def parse_section_excludes(md_body: str) -> dict[str, list[str]]:
    """
    Extrae los tags {{section_exclude: "kw1, kw2, ..."}} del .md.
    Retorna dict: {heading: [keywords_a_excluir]}
    Usar para secciones con contenido rico donde el Nivel 2 matchea bien,
    pero ciertas queries deben ser descartadas (ej: queries de código sobre
    una sección conceptual).
    """
    headings = parse_markdown_headings(md_body)
    result: dict[str, list[str]] = {}

    for match in SECTION_EXCLUDE_RE.finditer(md_body):
        raw = match.group(1)
        excludes = [k.strip() for k in raw.split(",") if k.strip()]
        tag_pos = match.start()
        heading = None
        for lvl, title, hpos in reversed(headings):
            if hpos < tag_pos:
                heading = title
                break
        if heading and excludes:
            result[heading] = excludes

    return result


def strip_section_meta(md: str) -> str:
    """Elimina tags semánticos antes de renderizar."""
    md = SECTION_META_RE.sub("", md)
    md = SECTION_EXCLUDE_RE.sub("", md)
    return md


@st.cache_data(show_spinner=False)
def module_label_from_md(stem: str, md_text: str) -> str:
    meta, _ = split_front_matter(md_text)

    # Los apéndices conservan su numeración editorial propia (A01, A02...),
    # aunque su frontmatter haya heredado un "module: 06/07" histórico.
    m = re.match(r"^(A\d{2})_(.+)$", stem, re.I)
    if m:
        return f"{m.group(1).upper()} · {m.group(2).replace('_', ' ')}"

    mod = str(meta.get("module", "")).strip()
    title = str(meta.get("title", "")).strip()
    if mod and title:
        mod2 = mod.zfill(2) if mod.isdigit() else mod
        return f"{mod2} · {title}"

    m = re.match(r"^(\d{2})_(.+)$", stem)
    if m:
        return f"{m.group(1)} · {m.group(2).replace('_', ' ')}"
    return stem


def extract_section_by_heading(md_body: str, heading: str, level: int | tuple[int, ...] = 2) -> str | None:
    """
    Extrae la sección del markdown que corresponde al heading dado.

    Maneja correctamente:
    - Prefijos numéricos [N] en el heading del MD
    - Emojis pegados a texto (🌟¿Qué)
    - Caracteres especiales: : ? ! ¿ ¡ . vs -
    - Múltiples niveles de heading
    """
    levels: Iterable[int] = (level,) if isinstance(level, int) else level

    def _match_key(text: str) -> str:
        """
        Normaliza headings para recuperar secciones aunque el heading visible use
        tags de tono ({dw-gold}), emojis o pequeñas diferencias de puntuacion.
        """
        text = html.unescape(text or "")
        text = _strip_heading_tone_tags(text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"^\s*\[\d+\]\s*", "", text)
        text = _norm(text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _extract_by_positions(target: str, lvl: int) -> str | None:
        target_keys = {_match_key(target)}
        target_keys.add(_match_key(re.sub(r"^\s*\[\d+\]\s*", "", target or "")))
        target_keys = {k for k in target_keys if k}
        if not target_keys:
            return None

        headings = parse_markdown_headings(md_body)
        for idx, (h_lvl, title, start) in enumerate(headings):
            if h_lvl != lvl:
                continue
            cand_keys = {_match_key(title)}
            cand_keys.add(_match_key(re.sub(r"^\s*\[\d+\]\s*", "", title or "")))
            cand_keys = {k for k in cand_keys if k}

            if not (target_keys & cand_keys):
                continue

            end = len(md_body)
            for next_lvl, _next_title, next_start in headings[idx + 1:]:
                if next_lvl <= h_lvl:
                    end = next_start
                    break
            return md_body[start:end].strip()

    def _extract(md: str, pattern_str: str, lvl: int):
        m = re.search(pattern_str, md, flags=re.M | re.UNICODE)
        if not m:
            return None
        start = m.start()
        m2 = re.search(rf"^#{{1,{lvl}}}\s+.+$", md[m.end():], flags=re.M)
        end = (m.end() + m2.start()) if m2 else len(md)
        return md[start:end].strip()

    def _escape(word: str) -> str:
        return re.sub(r'([.()\[\]{}+*\\^$|¿¡?!])', r'\\\1', word)

    def _tokenize(h: str) -> list[str]:
        h = re.sub(r'([𐀀-􏿿]|[☀-➿])([\w¿¡])', r'\1 \2', h)
        h = re.sub(r'([\w])([𐀀-􏿿]|[☀-➿])', r'\1 \2', h)
        return h.strip().split()

    for lvl in levels:
        # Estrategia 0: match por mapa de headings. Es la ruta mas robusta
        # para headings renderizados con tags visuales o assets html_embed.
        result0 = _extract_by_positions(heading, lvl)
        if result0:
            return result0

        hashes = '#' * lvl
        prefix = r'(?:\[\d+\]\s+)?'

        # Estrategia 1: match con prefijo [N] opcional y tokenización de emojis
        words = _tokenize(heading.strip())
        if words:
            pattern = (
                rf"^{hashes}\s+{prefix}"
                + r'\s*'.join(_escape(w) for w in words)
                + r'\s*$'
            )
            result = _extract(md_body, pattern, lvl)
            if result:
                return result

        # Estrategia 2: el heading pasado YA tiene prefijo [N], probar sin él
        stripped = re.sub(r'^\[\d+\]\s*', '', heading.strip())
        if stripped != heading.strip():
            words2 = _tokenize(stripped)
            if words2:
                pattern2 = (
                    rf"^{hashes}\s+{prefix}"
                    + r'\s*'.join(_escape(w) for w in words2)
                    + r'\s*$'
                )
                result = _extract(md_body, pattern2, lvl)
                if result:
                    return result

        # Estrategia 3: sufijo — últimas 3 palabras (más permisivo)
        if words:
            pattern3 = (
                rf"^{hashes}\s+.*"
                + r'\s+'.join(_escape(w) for w in words[-3:])
                + r'\s*$'
            )
            result = _extract(md_body, pattern3, lvl)
            if result:
                return result

    return None


def key_topics_from_module(md: str, level: int = 2, max_topics: int = 8):
    return key_topics_from_module_with_start(md, level=level, max_topics=max_topics, section_start=0)


def key_topics_from_module_with_start(md: str, level: int = 2, max_topics: int = 8, section_start: int = 0):
    meta, body = split_front_matter(md)

    kt = meta.get("key_topics", [])
    if isinstance(kt, list) and kt:
        topics = []
        for t in kt:
            if t.get("enabled", True):
                topics.append({"label": t.get("label", ""), "heading": t.get("heading", "")})
        return topics[:max_topics]

    headings = parse_markdown_headings(body)
    seen, out = set(), []
    section_num = section_start
    for lvl, t, start in headings:
        if lvl != level:
            continue

        display_title, section_num = _section_display_title(t, section_num)
        k = _norm(_strip_heading_number(_strip_heading_tone_tags(t)))
        if k in seen:
            continue
        seen.add(k)
        out.append({
            "label": _strip_heading_tone_tags(display_title),
            "heading": display_title,
        })
    return out[:max_topics]


def section_number_base_for_path(selected_path: str | Path) -> int:
    """
    Calcula cuántas secciones H2 existen antes del módulo seleccionado.

    La numeración visible del copiloto es global y editorial: empieza en 00_Intro
    y continúa por orden de archivos Markdown. No depende de números escritos en
    los headings, que se consideran legacy/ruido visual.
    """
    path = Path(selected_path)
    content_dir = path.parent
    root_dir = content_dir.parent if content_dir.name.lower() == "apendices" else content_dir
    total = 0
    md_paths = sorted(root_dir.glob("*.md"), key=lambda p: p.name.lower())
    appendices_dir = root_dir / "apendices"
    if appendices_dir.exists():
        md_paths.extend(sorted(appendices_dir.glob("*.md"), key=lambda p: p.name.lower()))

    for md_path in md_paths:
        if md_path.resolve() == path.resolve():
            break
        try:
            md_text = md_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        _meta, body = split_front_matter(md_text)
        total += sum(1 for lvl, _title, _pos in parse_markdown_headings(body) if lvl == 2)
    return total


def strip_first_h1(md: str) -> str:
    lines = md.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).lstrip()
    return md


def demote_markdown_headings(md: str, shift: int = 1) -> str:
    def repl(match):
        hashes = match.group(1)
        title = match.group(2)
        new_level = min(len(hashes) + shift, 6)
        return "#" * new_level + " " + title

    return re.sub(r"^(#{1,6})\s+(.+)$", repl, md, flags=re.M)


# =========================================================
# Custom markup tokens
# =========================================================

IMG_RE = re.compile(r"!\[(.*?)\]\((.*?)\)")
HTML_EMBED_RE = re.compile(
    r"^\s*\{\{\s*html_embed\s*:\s*([^\s}]+)([^\}]*)\}\}\s*$",
    re.I | re.M,
)
MEDIA_RE = re.compile(
    r"!\[(.*?)\]\((.*?)\)|^\s*\{\{\s*html_embed\s*:\s*([^\s}]+)([^\}]*)\}\}\s*$",
    re.I | re.M,
)
HTML_EMBED_PARAM_RE = re.compile(
    r'([A-Za-z0-9_\-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s}]+))',
    re.I,
)
TREE_BTN_RE = re.compile(r"^\s*\{\{\s*tree_button\s*:\s*(.+?)\s*\}\}\s*$", re.I)
TREE_BTN_KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^,\s]+))')

# section_meta: tag invisible para intent detection del asistente.
# No renderiza ningún contenido visual. Usarlo solo como señal de routing.
# Uso en .md: {{section_meta: keywords="ODP, framework ODP, ..."}}
SECTION_META_RE    = re.compile(r"^\s*\{\{\s*section_meta\s*:\s*(.*?)\s*\}\}\s*$", re.I | re.M)
SECTION_META_KV_RE = re.compile(r'keywords\s*=\s*"([^"]+)"', re.I)
SECTION_EXCLUDE_RE = re.compile(r"^\s*\{\{\s*section_exclude\s*:\s*\"(.*?)\"\s*\}\}\s*$", re.I | re.M)
SECTION_MODE_RE    = re.compile(r"^\s*\{\{\s*section_mode\s*:\s*(.*?)\s*\}\}\s*$", re.I | re.M)
SECTION_ID_RE      = re.compile(r"^\s*\{\{\s*section_id\s*:\s*([0-9]+)\s*\}\}\s*$", re.I | re.M)

# semantic_summary: bloque invisible en UI, pero conservado para el pipeline LLM.
# Usarlo cuando un asset visual (html_embed/imagen) contiene conocimiento técnico
# que no está suficientemente representado por el texto Markdown visible.
SEMANTIC_SUMMARY_RE = re.compile(
    r"^\s*\{\{\s*semantic_summary\s*\}\}\s*$.*?^\s*\{\{\s*/\s*semantic_summary\s*\}\}\s*$",
    re.I | re.M | re.S,
)

CALLOUT_RE = re.compile(r"\{\{callout:(.*?)\}\}(.*?)\{\{/callout\}\}", re.S | re.I)
CALLOUT_ROW_RE = re.compile(r"^\s*\{\{\s*callout_row\s*\}\}\s*$", re.M | re.I)
CALLOUT_ROW_END_RE = re.compile(r"^\s*\{\{\s*/\s*callout_row\s*\}\}\s*$", re.M | re.I)

LINK_RE = re.compile(r'<link\s+url="(.*?)"\s*>(.*?)</link>', re.I | re.S)

SUPPORT_LINE_RE = re.compile(r"^\s*\{\{\s*support\s*:\s*(.*?)\s*\}\}\s*$", re.I)
SUPPORT_KV_RE = re.compile(r'([A-Za-z0-9 _\-]+)\s*=\s*(?:"([^"]+)"|(true|false))', re.I)
BADGE_LINE_RE = re.compile(r"^\s*\{\{\s*badge\s*:\s*(.*?)\s*\}\}\s*$", re.I)
SUBTITLE_LINE_RE = re.compile(r"^\s*\{\{\s*subtitle\s*:\s*(.*?)\s*\}\}\s*$", re.I)
_HEADING_BADGES: dict[str, str] = {}
_CURRENT_SECTION_NUM = 0

# =========================================================
# Small HTML helpers
# =========================================================
HEADING_TONE_RE = re.compile(r"\{([^{}]+)\}")
HEADING_NUMBER_RE = re.compile(r"^\s*\[([0-9]+)\]\s*(.+?)\s*$")


def _strip_heading_tone_tags(text: str) -> str:
    return HEADING_TONE_RE.sub("", text or "")


def _strip_heading_number(text: str) -> str:
    match = HEADING_NUMBER_RE.match(text or "")
    return match.group(2).strip() if match else (text or "").strip()


def _section_display_title(title: str, current_num: int) -> tuple[str, int]:
    """
    Devuelve el titulo visible con numeracion de dos digitos.

    Contrato:
    - La numeración visible se calcula por orden real desde 00_Intro.
    - Los [NN] escritos en headings son legacy y se ignoran visualmente.
    - {{section_id: NN}} se ignora por la misma razón; queda solo como tag legacy.
    """
    num = current_num + 1
    clean_title = _strip_heading_number((title or "").strip())
    width = max(1, int(SECTION_NUMBER_WIDTH or 2))
    return f"[{num:0{width}d}] {clean_title}", num


def _heading_tone_html(text: str) -> str:
    parts: list[str] = []
    pos = 0
    active_class = ""

    for match in HEADING_TONE_RE.finditer(text or ""):
        chunk = text[pos:match.start()]
        if chunk:
            safe = html.escape(chunk)
            if active_class:
                parts.append(f'<span class="{active_class}">{safe}</span>')
            else:
                parts.append(safe)
        token = match.group(1).strip().lower()
        active_class = "dw-heading-tone--" + token.removeprefix("dw-") if token.startswith("dw-") else ""
        pos = match.end()

    tail = (text or "")[pos:]
    if tail:
        safe_tail = html.escape(tail)
        if active_class:
            parts.append(f'<span class="{active_class}">{safe_tail}</span>')
        else:
            parts.append(safe_tail)

    return "".join(parts)


def _slugify_heading(text: str) -> str:
    """
    Genera un id/hash estable para headings.
    Ej: "[18] ✔️ Clean Core aplicado a extracción"
    -> "18-clean-core-aplicado-a-extraccion"
    Delega a core.utils.slugify — fuente de verdad única.
    """
    from core.utils import slugify as _slugify
    return _slugify(_strip_heading_tone_tags(text))


def _extract_heading_badges(md: str) -> tuple[str, dict[str, str]]:
    badges: dict[str, str] = {}
    out: list[str] = []
    pending_heading: str | None = None

    for line in (md or "").splitlines():
        m_hdr = re.match(r"^(#{2,6})\s+(.+)$", line.strip())
        if m_hdr:
            pending_heading = m_hdr.group(2).strip()
            out.append(line)
            continue

        m_badge = BADGE_LINE_RE.match(line.strip())
        if m_badge and pending_heading:
            badge = (m_badge.group(1) or "").strip().strip('"').strip("'")
            if badge:
                badges[_slugify_heading(pending_heading)] = badge
            continue

        if line.strip() and not SUPPORT_LINE_RE.match(line.strip()):
            pending_heading = None
        out.append(line)

    return "\n".join(out), badges


def _render_raw_html(html_src: str) -> None:
    if hasattr(st, "html"):
        st.html(html_src)
    else:
        st.markdown(html_src, unsafe_allow_html=True)


def _header_with_support_html(title: str, right_html: str, badge: str = "", extra_class: str = "", display_title: str = "") -> str:
    """
    Header custom con:
    - anchor visual tipo 'candadito/link'
    - query param (?section=slug) para que app.py pueda abrir 'Sección seleccionada'
    """
    slug = _slugify_heading(title)
    safe_title = html.escape(display_title or title)

    return f"""
<div class="dw-md-h2" id="{slug}">
  <div class="dw-md-title">
    {safe_title}
    <a class="dw-md-anchor" href="#{slug}" aria-label="Ir a esta sección">🔗</a>
  </div>
  {right_html}
</div>
""".strip()

def _header_with_support_html(title: str, right_html: str, badge: str = "", extra_class: str = "", display_title: str = "") -> str:
    slug = _slugify_heading(title)
    badge = badge or _HEADING_BADGES.get(slug, "")
    hero_num = ""
    title_for_display = (display_title or title).strip()
    hero_text = _heading_tone_html(title_for_display)
    m_num = re.match(r"^\s*(\[[^\]]+\])\s*(.+?)\s*$", title_for_display)
    if m_num:
        hero_num = f'<span class="dw-page-hero__num">{html.escape(m_num.group(1))}</span>'
        hero_text = _heading_tone_html(m_num.group(2))

    safe_badge = html.escape((badge or "").strip())
    badge_html = f'<div class="dw-page-hero__badge">{safe_badge}</div>' if safe_badge else ""

    class_attr = "dw-page-hero" + (f" {extra_class}" if extra_class else "")

    return f"""
<section class="{class_attr}" id="{slug}">
  <div class="dw-page-hero__dots" aria-hidden="true">
    <span></span><span></span><span></span>
    <span></span><span></span><span></span>
    <span></span><span></span><span></span>
    <span></span><span></span><span></span>
    <span></span><span></span><span></span>
  </div>
  <div class="dw-page-hero__content">
    {badge_html}
    <div class="dw-page-hero__title">{hero_num}<span>{hero_text}</span><a class="dw-md-anchor" href="#{slug}" aria-label="Ir a esta sección">🔗</a></div>
  </div>
  {right_html}
</section>
""".strip()


def _support_asset_download(item: dict, support_assets: list[dict] | None) -> dict | None:
    """
    Resuelve referencias de {{support}} hacia intent_assets declarados en el
    front matter del módulo.

    Contrato:
      intent_assets:
        - description: "Template CDS..."
          asset: "templates/cds_extractor_template.abap"

      {{support: Template CDS="asset:templates/cds_extractor_template.abap"}}
      {{support: Template CDS="intent_asset:Template CDS"}}

    Seguridad:
    - Solo se habilitan assets ya declarados en intent_assets.
    - La ruta final debe quedar dentro de BASE_DIR.
    - El link se emite como data URL descargable, no como ruta local pública.
    """
    raw_ref = (item.get("url") or "").strip()
    if not raw_ref.lower().startswith(("asset:", "intent_asset:", "intent:")):
        return None

    ref = raw_ref.split(":", 1)[1].strip()
    if not ref or not support_assets:
        return None

    ref_norm = _norm(ref)
    match = None
    for asset in support_assets:
        asset_path = str(asset.get("asset") or "").strip()
        description = str(asset.get("description") or "").strip()
        candidates = {
            _norm(asset_path),
            _norm(Path(asset_path).name),
            _norm(description),
        }
        if ref_norm in candidates or any(ref_norm and ref_norm in c for c in candidates):
            match = asset
            break

    if not match:
        return None

    from config import BASE_DIR
    rel_path = str(match.get("asset") or "").strip()
    try:
        asset_path = (BASE_DIR / rel_path).resolve()
        base_dir = BASE_DIR.resolve()
        if base_dir not in asset_path.parents and asset_path != base_dir:
            return None
        if not asset_path.exists() or not asset_path.is_file():
            return None
        data = asset_path.read_bytes()
    except Exception:
        return None

    mime = "text/plain"
    return {
        "label": (item.get("label") or match.get("description") or asset_path.name),
        "data": data,
        "mime": mime,
        "download": asset_path.name,
        "title": match.get("description") or item.get("label") or asset_path.name,
    }


def _support_asset_downloads(items: list[dict], support_assets: list[dict] | None) -> list[dict]:
    downloads: list[dict] = []
    for it in items or []:
        asset_download = _support_asset_download(it, support_assets)
        if asset_download:
            downloads.append(asset_download)
    return downloads


def _support_links_html(
    items: list[dict],
    *,
    pin: bool = False,
    section_slug: str = "",
    support_assets: list[dict] | None = None,
) -> str:
    pills: list[str] = []

    for it in items:
        label = (it.get("label") or "").strip()
        url = (it.get("url") or "").strip()
        if _support_asset_download(it, support_assets):
            continue
        if not label or not url:
            continue
        l = label.lower()
        u = url.lower()
        if "template" in l:
            pills.append(_pill(_compact_template_label(label), url, "\U0001F4C4", title=label))
            continue
        if "help" in l or "help.sap.com" in u:
            pills.append(_pill("SAP Help", url, "🔗", title=label))
        elif "note" in l or "nota" in l or "me.sap.com/notes" in u:
            pills.append(_pill(label, url, "📝"))
        elif "appendix" in l or "apendice" in l or "danwor.sharepoint.com/" in u:
            pills.append(_pill(label, url, "👨🏻‍🏫"))
        else:
            pills.append(_pill(label, url, "🌐"))

    if pin and section_slug:
        safe_slug = html.escape(section_slug, quote=True)
        pills.append(
            f'<span class="dw-md-pill dw-md-pill--icon" '
            f'style="pointer-events:none;user-select:none" '
            f'aria-hidden="true">📌</span>'
        )
    return f'<div class="dw-md-links">{"".join(pills)}</div>' if pills else ""

def _render_pinnable_header(
    title: str,
    right_html: str,
    *,
    key_prefix: str,
    idx: int,
    badge: str = "",
    first_header: bool = False,
    display_title: str = "",
    show_pinner: bool = True,
    asset_downloads: list[dict] | None = None,
) -> int:
    slug = _slugify_heading(title)
    pin_key = f"{key_prefix}_pin_{idx}"
    font_size = int(st.session_state.get(_FONT_SIZE_KEY, _FONT_DEFAULT))
    header_extra_class = "dw-page-hero--first" if first_header else ""
    if not show_pinner:
        header_extra_class = (header_extra_class + " dw-page-hero--no-pinner").strip()

    controls_right_html = right_html if show_pinner else ""
    header_right_html = "" if show_pinner else right_html

    header_html = _header_with_support_html(
        title,
        header_right_html,
        badge=badge,
        extra_class=header_extra_class,
        display_title=display_title,
    )

    if not show_pinner:
        _render_raw_html(header_html)
        return idx + 1

    has_right_controls = bool(controls_right_html or asset_downloads)
    if has_right_controls:
        col_hdr, col_links, col_down, col_up, col_pin = st.columns(
            [0.56, 0.32, 0.04, 0.04, 0.04],
            vertical_alignment="center",
            gap="small",
        )
    else:
        col_hdr, col_down, col_up, col_pin = st.columns(
            [0.875, 0.04, 0.04, 0.04],
            vertical_alignment="center",
            gap="small",
        )
    with col_hdr:
        _render_raw_html(header_html)
    if has_right_controls:
        with col_links:
            _render_header_support_controls(
                controls_right_html,
                asset_downloads or [],
                key_prefix=key_prefix,
                idx=idx,
            )
    with col_down:
        if st.button(
            "🗚",
            key=f"{key_prefix}_font_down_{idx}",
            help=f"Reducir texto ({font_size}px)",
            use_container_width=True,
            disabled=font_size <= _FONT_MIN,
        ):
            st.session_state[_FONT_SIZE_KEY] = font_size - 1
            st.rerun()
    with col_up:
        if st.button(
            "🗛",
            key=f"{key_prefix}_font_up_{idx}",
            help=f"Aumentar texto ({font_size}px)",
            use_container_width=True,
            disabled=font_size >= _FONT_MAX,
        ):
            st.session_state[_FONT_SIZE_KEY] = font_size + 1
            st.rerun()
    with col_pin:
        if st.button("📌", key=pin_key, help="Abrir en panel Sección"):
            st.session_state["selected_topic_from_anchor"] = display_title or title
            st.session_state["_scroll_to_slug"] = slug
            AppState.set_active_tools_tab(0)
            AppState.open_tools_panel()
            st.rerun()

    return idx + 1


def _render_header_support_controls(
    html_src: str,
    asset_downloads: list[dict],
    *,
    key_prefix: str,
    idx: int,
) -> None:
    if not asset_downloads:
        _render_raw_html(html_src)
        return

    cols = st.columns([0.46] * len(asset_downloads) + ([1.0] if html_src else []), gap="small")
    for n, asset_download in enumerate(asset_downloads):
        with cols[n]:
            st.download_button(
                label=str(asset_download.get("label") or asset_download.get("download") or "Descargar"),
                data=asset_download.get("data") or b"",
                file_name=str(asset_download.get("download") or "asset.txt"),
                mime=str(asset_download.get("mime") or "text/plain"),
                key=f"{key_prefix}_support_asset_{idx}_{n}",
                help=str(asset_download.get("title") or ""),
                use_container_width=True,
            )
    if html_src:
        with cols[-1]:
            _render_raw_html(html_src)


def _render_support_header(
    title: str,
    support: dict,
    *,
    key_prefix: str,
    idx: int,
    badge: str = "",
    first_header: bool = False,
    display_title: str = "",
    show_pinner: bool = True,
    support_assets: list[dict] | None = None,
) -> int:
    # El pinner es comportamiento nativo de todo encabezado. El tag {{support}}
    # solo agrega recursos complementarios a la derecha del mismo header.
    links = support.get("links", [])
    asset_downloads = _support_asset_downloads(links, support_assets)
    right_html = _support_links_html(
        links,
        pin=False,
        section_slug=_slugify_heading(title),
        support_assets=support_assets,
    )
    return _render_pinnable_header(
        title,
        right_html,
        key_prefix=key_prefix,
        idx=idx,
        badge=badge,
        first_header=first_header,
        display_title=display_title,
        show_pinner=show_pinner,
        asset_downloads=asset_downloads,
    )

def _inline_body_html(text: str) -> str:
    parts: list[str] = []
    pos = 0

    def _basic_inline(chunk: str) -> str:
        safe = html.escape(chunk or "")
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        return safe

    for m in LINK_RE.finditer(text or ""):
        before = text[pos:m.start()]
        if before:
            parts.append(_basic_inline(before))

        url = html.escape((m.group(1) or "").strip(), quote=True)
        label = _basic_inline((m.group(2) or "").strip())
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
        )
        pos = m.end()

    tail = (text or "")[pos:]
    if tail:
        parts.append(_basic_inline(tail))

    return "".join(parts)


def _callout_body_html(body: str) -> str:
    """
    Render seguro del body del callout:
    - preserva texto en párrafos
    - convierte líneas "- ..." en listas HTML
    - convierte <link url="...">Texto</link> en <a ...>
    """
    lines = (body or "").strip().splitlines()
    parts: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        text = " ".join(line.strip() for line in paragraph if line.strip()).strip()
        if text:
            parts.append(f"<p>{_inline_body_html(text)}</p>")
        paragraph = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            items = "".join(f"<li>{_inline_body_html(item)}</li>" for item in bullets)
            parts.append(f'<ul class="dw-callout-list">{items}</ul>')
        bullets = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_bullets()
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            bullets.append(bullet.group(1).strip())
            continue

        flush_bullets()
        paragraph.append(stripped)

    flush_paragraph()
    flush_bullets()
    return "".join(parts)

def _render_links_as_markdown(text: str) -> str:
    def repl(m: re.Match) -> str:
        url = (m.group(1) or "").strip()
        label = (m.group(2) or "").strip()
        return f"[{label}]({url})"
    return LINK_RE.sub(repl, text)


def _subtitle_blockquotes_to_html(text: str) -> str:
    """Render simple Markdown blockquotes as the roadmap-style subtitle class."""
    out: list[str] = []
    quote_buf: list[str] = []
    quote_no_gap = False
    pending_no_gap = False

    def flush_quote() -> None:
        nonlocal quote_no_gap
        if not quote_buf:
            return
        body = "<br>".join(html.escape(part.strip()) for part in quote_buf if part.strip())
        if body:
            extra_class = " dw-hero-desc--no-gap" if quote_no_gap else ""
            out.append(f'<p class="dw-hero-desc{extra_class}">{body}</p>')
        quote_buf.clear()
        quote_no_gap = False

    for line in text.splitlines():
        m_subtitle = SUBTITLE_LINE_RE.match(line.strip())
        if m_subtitle:
            flush_quote()
            raw_mode = (m_subtitle.group(1) or "").strip().lower()
            # Modos editoriales soportados:
            # - with-gap: comportamiento por defecto, conserva el margen izquierdo.
            # - no-gap: alinea el subtítulo al margen del contenido.
            pending_no_gap = raw_mode in {"no-gap", "nogap", "no_gap", "left", "flush"}
            continue

        match = re.match(r"^\s*>\s?(.*)$", line)
        if match:
            if not quote_buf:
                quote_no_gap = pending_no_gap
                pending_no_gap = False
            quote_buf.append(match.group(1))
            continue
        flush_quote()
        out.append(line)

    flush_quote()
    return "\n".join(out)


def _parse_support_items(raw: str) -> dict:
    """
    Soporta:
      {{support: SAP Help="https://..." Nota SAP="https://..." Pin=true}}

    Devuelve:
      {
        "links": [{"label": "...", "url": "..."}],
        "pin": True/False
      }
    """
    out = {
        "links": [],
        "pin": False,
    }

    for m in SUPPORT_KV_RE.finditer(raw or ""):
        key = (m.group(1) or "").strip()
        quoted_val = (m.group(2) or "").strip()
        bool_val = (m.group(3) or "").strip().lower()

        if key.lower() == "pin":
            out["pin"] = (bool_val == "true")
            continue

        if quoted_val:
            out["links"].append({
                "label": key,
                "url": quoted_val,
            })

    return out

def _pill(label: str, url: str, icon: str, *, title: str = "", download: str = "") -> str:
    safe_label = html.escape(label or "")
    safe_url = html.escape(url or "", quote=True)
    safe_icon = "" if download else html.escape(icon or "")
    safe_title = html.escape(title or label or "", quote=True)
    safe_download = html.escape(download or "", quote=True)
    download_attr = f' download="{safe_download}"' if safe_download else ""
    target_attr = "" if safe_download else ' target="_blank"'
    icon_html = f"{safe_icon} " if safe_icon else ""
    return (
        f'<a class="dw-md-pill" href="{safe_url}"{target_attr}{download_attr} '
        f'rel="noopener noreferrer" title="{safe_title}">{icon_html}{safe_label}</a>'
    )


def _compact_template_label(label: str) -> str:
    raw = (label or "").strip()
    normalized = raw.lower()
    if "ddic" in normalized:
        return "CDS DDIC"
    if "entity" in normalized:
        return "CDS Entity"
    if "cds" in normalized:
        return "CDS Template"
    return "Template"

def _callout_html(title: str, tone: str, icon: str, body: str) -> str:
    safe_title = html.escape(title or "")
    safe_icon = html.escape(icon or "")
    body_html = _callout_body_html(body)

    tone = (tone or "note").lower()
    tone_cls = {
        "note": "dw-callout--note",
        "success": "dw-callout--success",
        "punch": "dw-callout--punch",
        "warn": "dw-callout--warn",
    }.get(tone, "dw-callout--note")

    return f"""
<div class="dw-callout {tone_cls}">
  <div class="dw-callout__head">
    <span class="dw-callout__icon">{safe_icon}</span>
    <span class="dw-callout__title">{safe_title}</span>
  </div>
  <div class="dw-callout__body">{body_html}</div>
</div>
""".strip()

# =========================================================
# Rendering primitives
# =========================================================

def _render_text_with_tree_buttons(text: str, *, key_prefix: str, start_idx: int) -> int:
    """
    Renderiza markdown normal, pero intercepta líneas:
      {{tree_button: Título}}
    para abrir panel de árboles a la derecha.
    """
    idx = start_idx
    buf: list[str] = []

    for line in text.splitlines():
        m = TREE_BTN_RE.match(line)
        if not m:
            buf.append(line)
            continue

        if "\n".join(buf).strip():
            st.markdown(_subtitle_blockquotes_to_html("\n".join(buf)), unsafe_allow_html=True)
        buf = []

        tree_id, tree_title = _parse_tree_button_spec(m.group(1).strip())
        if st.button(
            f"🌳 Ejecutar árbol: {tree_title}",
            use_container_width=True,
            key=f"{key_prefix}_treebtn_{idx}",
        ):
            if tree_id:
                st.session_state["_requested_tree_id"] = tree_id
            st.session_state["_requested_tree_title"] = tree_title
            AppState.open_tree(tree_title)
            st.rerun()

        idx += 1

    if "\n".join(buf).strip():
        st.markdown(_subtitle_blockquotes_to_html("\n".join(buf)), unsafe_allow_html=True)

    return idx


def _parse_tree_button_spec(raw: str) -> tuple[str, str]:
    """
    Contrato del tag:
      {{tree_button: Titulo visible}}                         # legacy
      {{tree_button: id=extraction_options label="Titulo"}}   # reusable por id
      {{tree_button: extraction_options | Titulo visible}}     # shorthand

    El id permite invocar un arbol desde cualquier modulo/apendice sin
    duplicarlo ni atarlo al modulo actual.
    """
    spec = (raw or "").strip()
    if not spec:
        return "", ""

    if "|" in spec and "=" not in spec.split("|", 1)[0]:
        tree_id, label = [p.strip() for p in spec.split("|", 1)]
        return tree_id, label or tree_id

    kv: dict[str, str] = {}
    for m in TREE_BTN_KV_RE.finditer(spec):
        kv[m.group(1).strip().lower()] = (m.group(2) or m.group(3) or m.group(4) or "").strip()

    if kv:
        tree_id = kv.get("id") or kv.get("tree") or kv.get("tree_id") or ""
        label = kv.get("label") or kv.get("title") or tree_id
        return tree_id.strip(), label.strip()

    return "", spec


def tree_button_refs_from_markdown(md: str) -> list[dict[str, str]]:
    """
    Devuelve los árboles declarados explícitamente en el contenido actual.

    Esto permite que un módulo/apéndice exponga árboles reutilizables en el
    panel derecho sin duplicar YAML ni cambiar el módulo dueño del árbol.
    """
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in (md or "").splitlines():
        m = TREE_BTN_RE.match(line)
        if not m:
            continue
        tree_id, label = _parse_tree_button_spec(m.group(1).strip())
        key = (tree_id, label)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"id": tree_id, "label": label})
    return refs


def _render_text_with_callouts_tree_support(
    text: str,
    *,
    key_prefix: str,
    start_idx: int,
    show_pinners: bool = True,
    support_assets: list[dict] | None = None,
) -> int:
    """
    Pipeline único:
      - callouts / callout_row
      - support line debajo de H2..H6
      - <link url="..."> -> markdown
      - tree_button
      - markdown normal
    """
    global _CURRENT_SECTION_NUM

    lines = text.splitlines()
    i = 0
    idx = start_idx

    in_row = False
    row_blocks: list[str] = []
    normal_buf: list[str] = []
    section_header_seen = start_idx > 0

    def flush_normal():
        nonlocal idx, normal_buf
        if not normal_buf:
            return
        chunk = "\n".join(normal_buf)
        chunk = _render_links_as_markdown(chunk)
        idx = _render_text_with_tree_buttons(chunk, key_prefix=key_prefix, start_idx=idx)
        normal_buf = []

    def flush_row():
        nonlocal in_row, row_blocks
        if not row_blocks:
            in_row = False
            return

        if len(row_blocks) == 1:
            st.markdown(row_blocks[0], unsafe_allow_html=True)
        else:
            st.markdown(
                f"""
<div class="dw-callout-row">
  <div class="dw-callout-col">{row_blocks[0]}</div>
  <div class="dw-callout-col">{row_blocks[1]}</div>
</div>
""".strip(),
                unsafe_allow_html=True,
            )
        row_blocks = []
        in_row = False

    while i < len(lines):
        line = lines[i]

        # --- semantic_summary block: invisible en UI.
        # El asistente lo consume desde el Markdown original en core/prompts.py.
        if re.match(r"^\s*\{\{\s*semantic_summary\s*\}\}\s*$", line, flags=re.I):
            i += 1
            while i < len(lines) and not re.match(r"^\s*\{\{\s*/\s*semantic_summary\s*\}\}\s*$", lines[i], flags=re.I):
                i += 1
            if i < len(lines):
                i += 1
            continue

        # --- callout row start/end
        if CALLOUT_ROW_RE.match(line):
            flush_normal()
            flush_row()
            in_row = True
            i += 1
            continue

        if CALLOUT_ROW_END_RE.match(line):
            flush_normal()
            flush_row()
            i += 1
            continue

        # --- callout block (desde el inicio del "rest")
        rest = "\n".join(lines[i:])
        m_call = CALLOUT_RE.match(rest)
        if m_call:
            flush_normal()

            attrs_raw = m_call.group(1) or ""
            body = m_call.group(2) or ""
            attrs = {}
            for m in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', attrs_raw):
                attrs[m.group(1).lower()] = m.group(2)

            block = _callout_html(
                title=attrs.get("title", ""),
                tone=attrs.get("tone", "note"),
                icon=attrs.get("icon", ""),
                body=body,
            )

            if in_row:
                row_blocks.append(block)
                if len(row_blocks) >= 2:
                    flush_row()
            else:
                st.markdown(block, unsafe_allow_html=True)

            consumed = rest[: m_call.end()].splitlines()
            i += len(consumed)
            continue

        # --- support: heading + next-line support (tolerando líneas vacías intermedias)
        m_hdr = re.match(r"^(#{2,6})\s+(.+)$", line.strip())
        if m_hdr:
            # Buscar la próxima línea no vacía después del heading
            _next_i = i + 1
            while _next_i < len(lines) and not lines[_next_i].strip():
                _next_i += 1
            if _next_i < len(lines):
                m_sid = SECTION_ID_RE.match(lines[_next_i].strip())
                if m_sid:
                    _next_i += 1
                    while _next_i < len(lines) and not lines[_next_i].strip():
                        _next_i += 1
            m_sup = SUPPORT_LINE_RE.match(lines[_next_i]) if _next_i < len(lines) else None
            if m_sup:
                flush_normal()
                flush_row()
                is_first_header = not section_header_seen
                if section_header_seen:
                    _render_raw_html('<div class="dw-section-break" aria-hidden="true"></div>')
                section_header_seen = True

                title = m_hdr.group(2).strip()
                level = len(m_hdr.group(1))
                if level in (2, 4):
                    display_title, _CURRENT_SECTION_NUM = _section_display_title(title, _CURRENT_SECTION_NUM)
                else:
                    display_title = title
                support = _parse_support_items(m_sup.group(1) or "")

                # si no parsea nada, lo dejamos pasar normal
                if not support.get("links") and not support.get("pin"):
                    normal_buf.append(line)
                    i += 1
                    continue

                idx = _render_support_header(
                    title,
                    support,
                    key_prefix=key_prefix,
                    idx=idx,
                    first_header=is_first_header,
                    display_title=display_title,
                    show_pinner=show_pinners,
                    support_assets=support_assets,
                )

                # Consumimos heading + líneas vacías + support line
                i = _next_i + 1
                continue

        # --- heading sin support: renderizar como custom header con id
        # para que el scroll desde el asistente funcione igual que con support
        if m_hdr:
            flush_normal()
            flush_row()
            is_first_header = not section_header_seen
            if section_header_seen:
                _render_raw_html('<div class="dw-section-break" aria-hidden="true"></div>')
            section_header_seen = True
            title = m_hdr.group(2).strip()
            level = len(m_hdr.group(1))
            if level in (2, 4):
                display_title, _CURRENT_SECTION_NUM = _section_display_title(title, _CURRENT_SECTION_NUM)
            else:
                display_title = title
            idx = _render_pinnable_header(
                title,
                "",
                key_prefix=key_prefix,
                idx=idx,
                first_header=is_first_header,
                display_title=display_title,
                show_pinner=show_pinners,
            )
            i += 1
            continue

        # tags semánticos sueltos: se ignoran visualmente.
        if SUPPORT_LINE_RE.match(line) or SECTION_META_RE.match(line) or SECTION_MODE_RE.match(line) or SECTION_ID_RE.match(line):
            i += 1
            continue

        # normal
        normal_buf.append(line)
        i += 1

    flush_normal()
    flush_row()
    return idx


# =========================================================
# Main renderer: imágenes + resto del pipeline
# =========================================================

def _resolve_content_path(raw_path: str, base_dir: Path) -> Path:
    path = Path((raw_path or "").strip().strip('"').strip("'"))
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _inline_local_stylesheets(html_src: str, html_dir: Path) -> str:
    """Inline local CSS links so Streamlit iframe embeds remain self-contained."""
    link_re = re.compile(
        r'<link\s+rel=["\']stylesheet["\']\s+href=["\']([^"\']+\.css)["\']\s*/?>',
        re.I,
    )

    def repl(match: re.Match) -> str:
        href = (match.group(1) or "").strip()
        if re.match(r"^[a-z]+://", href, flags=re.I) or href.startswith("//"):
            return match.group(0)
        css_path = (html_dir / href).resolve()
        if not css_path.exists():
            return match.group(0)
        css_src = css_path.read_text(encoding="utf-8", errors="ignore")
        safe_href = html.escape(href, quote=True)
        return f'<style data-inlined-from="{safe_href}">\n{css_src}\n</style>'

    return link_re.sub(repl, html_src)


def _parse_html_embed_params(raw_params: str) -> dict[str, str]:
    """
    Parse html_embed parameters.

    Markdown authors can keep semantic sections separate while reusing the same
    visual asset, e.g.:
      {{html_embed: ../assets/foo.html height=780 default_mode=dac}}

    ``height`` is consumed by Streamlit; all other params are injected into the
    iframe as ``window.DANWOR_EMBED_PROPS`` for the embedded HTML to read.
    """
    params: dict[str, str] = {}
    for match in HTML_EMBED_PARAM_RE.finditer(raw_params or ""):
        key = (match.group(1) or "").strip().lower().replace("-", "_")
        val = next((g for g in match.groups()[1:] if g is not None), "")
        if key:
            params[key] = (val or "").strip()
    return params


def _html_embed_height(params: dict[str, str]) -> int | None:
    raw_height = (params.get("height") or "").strip()
    if not raw_height:
        return None
    try:
        return int(raw_height)
    except ValueError:
        return None


def _camelize_param_key(key: str) -> str:
    parts = [p for p in re.split(r"[_\-]+", key or "") if p]
    if not parts:
        return key
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _inject_html_embed_props(html_src: str, params: dict[str, str]) -> str:
    props: dict[str, str] = {}
    for key, val in (params or {}).items():
        if key == "height":
            continue
        props[key] = val
        props[_camelize_param_key(key)] = val

    if not props:
        return html_src

    payload = json.dumps(props, ensure_ascii=False).replace("</", "<\\/")
    script = f"<script>window.DANWOR_EMBED_PROPS = {payload};</script>"
    if re.search(r"</head>", html_src, re.I):
        return re.sub(r"</head>", script + "\n</head>", html_src, count=1, flags=re.I)
    return script + "\n" + html_src


def _render_html_embed(raw_path: str, base_dir: Path, height: int | None = None, params: dict[str, str] | None = None) -> None:
    embed_path = _resolve_content_path(raw_path, base_dir)
    if not embed_path.exists():
        st.warning(f"No se encontró el recurso HTML: {embed_path}")
        return

    html_src = embed_path.read_text(encoding="utf-8", errors="ignore")
    html_src = _inline_local_stylesheets(html_src, embed_path.parent)
    html_src = _inject_html_embed_props(html_src, params or {})
    st.components.v1.html(
        html_src,
        height=height or 620,
        scrolling=True,
    )


def render_md_with_image_zoom(
    md: str,
    base_dir: str | Path = ".",
    key_prefix: str = "mdimg",
    section_start: int = 0,
    show_pinners: bool = True,
    intent_assets: list[dict] | None = None,
):
    """
    Renderiza markdown con:
      - imágenes ![alt](path) como st.image (resolviendo relativo a base_dir)
      - callouts/callout_row, support, tree_button, links custom en el resto del texto
    """
    global _CURRENT_SECTION_NUM

    base_dir = Path(base_dir)
    _CURRENT_SECTION_NUM = section_start

    # Zoom (si lo reactivás a futuro)
    if st.session_state.get("zoom_img_path"):
        img_path = st.session_state["zoom_img_path"]
        caption = st.session_state.get("zoom_img_caption", "")

        if hasattr(st, "dialog"):
            @st.dialog("Vista ampliada")
            def _zoom_dialog():
                st.image(img_path, caption=caption, use_container_width=True)
            _zoom_dialog()
        else:
            with st.expander("Vista ampliada", expanded=True):
                st.image(img_path, caption=caption, use_container_width=True)

        st.session_state["zoom_img_path"] = None
        st.session_state["zoom_img_caption"] = None

    # Eliminar tags section_meta antes de renderizar — son solo para intent detection
    md = strip_section_meta(md)
    global _HEADING_BADGES
    md, _HEADING_BADGES = _extract_heading_badges(md)

    pos = 0
    idx = 0

    for m in MEDIA_RE.finditer(md):
        chunk = md[pos:m.start()]
        if chunk.strip():
            idx = _render_text_with_callouts_tree_support(
                chunk,
                key_prefix=key_prefix,
                start_idx=idx,
                show_pinners=show_pinners,
                support_assets=intent_assets,
            )

        if m.group(3):
            raw_path = (m.group(3) or "").strip()
            embed_params = _parse_html_embed_params(m.group(4) or "")
            embed_height = _html_embed_height(embed_params)
            _render_html_embed(raw_path, base_dir, embed_height, embed_params)
        else:
            alt_text = (m.group(1) or "").strip()
            raw_path = (m.group(2) or "").strip()
            img_path = _resolve_content_path(raw_path, base_dir)
            st.image(str(img_path), caption=alt_text if alt_text else None, use_container_width=True)

        pos = m.end()
        idx += 1

    tail = md[pos:]
    if tail.strip():
        _render_text_with_callouts_tree_support(
            tail,
            key_prefix=key_prefix,
            start_idx=idx,
            show_pinners=show_pinners,
            support_assets=intent_assets,
        )
