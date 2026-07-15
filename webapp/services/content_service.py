from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import html
import json
import re
from urllib.parse import urlencode

import markdown
import yaml

from core.content_registry import (
    iter_content_academy_paths,
    iter_content_appendix_paths,
    iter_content_module_paths,
    resolve_content_path,
)
from core.utils import norm_text as _norm, slugify

FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
META_RE = re.compile(r"^\s*\{\{(?:section_meta|section_exclude|section_id|section_mode|subtitle)\s*:.*?\}\}\s*$", re.I | re.M)
SEMANTIC_RE = re.compile(r"^\s*\{\{semantic_summary\}\}.*?^\s*\{\{/semantic_summary\}\}\s*$", re.I | re.M | re.S)
EMBED_RE = re.compile(r"^\s*\{\{html_embed\s*:\s*([^\s}]+)([^}]*)\}\}\s*$", re.I | re.M)
EMBED_PARAM_RE = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s}]+))', re.I)
SUPPORT_RE = re.compile(r"^\s*\{\{support\s*:\s*(.*?)\}\}\s*$", re.I | re.M)
LEADING_SUPPORT_RE = re.compile(r"\A\s*(\{\{support\s*:\s*.*?\}\})\s*", re.I)
SUPPORT_ITEM_RE = re.compile(r'([A-Za-z0-9 _-]+)\s*=\s*"([^"]+)"')
TONE_RE = re.compile(r"\{dw-(black|gold|white|green|red)\}", re.I)
CALLOUT_RE = re.compile(r"\{\{callout:(.*?)\}\}(.*?)\{\{/callout\}\}", re.I | re.S)
CALLOUT_ROW_RE = re.compile(
    r"^\s*\{\{\s*callout_row\s*\}\}\s*$(.*?)^\s*\{\{\s*/\s*callout_row\s*\}\}\s*$",
    re.I | re.M | re.S,
)
CALLOUT_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
CUSTOM_LINK_RE = re.compile(r'<link\s+url="(.*?)"\s*>(.*?)</link>', re.I | re.S)
TREE_BUTTON_RE = re.compile(r"^\s*\{\{\s*tree_button\s*:\s*(.+?)\s*\}\}\s*$", re.I | re.M)
INTERNAL_TOKEN_LINE_RE = re.compile(r"^\s*\{\{.*?\}\}\s*$", re.M)
PIPE_LINK_RE = re.compile(r"^([ \t]*(?:[-*]\s+)?)?(.+?)\s*\|\s*\[Link\]\((https?://[^)]+)\)\s*$", re.I | re.M)


@dataclass(frozen=True)
class ModuleSummary:
    stem: str
    number: str
    title: str
    nav_label: str
    path: Path
    # Página fuera de la navegación (frontmatter nav_hidden: true). Sigue siendo
    # resoluble por URL directa y válida para feedback; solo no se lista en el nav.
    nav_hidden: bool = False


@dataclass(frozen=True)
class ModuleSection:
    number: int
    title: str
    slug: str
    html: str
    markdown: str


@dataclass(frozen=True)
class ModuleDocument:
    summary: ModuleSummary
    scope: str
    guardrails: tuple[str, ...]
    sections: tuple[ModuleSection, ...]
    source: str


def split_front_matter(source: str) -> tuple[dict, str]:
    match = FRONT_RE.match(source)
    if not match:
        return {}, source
    return yaml.safe_load(match.group(1)) or {}, source[match.end():]


def _summary(path: Path) -> ModuleSummary:
    meta, _ = split_front_matter(path.read_text(encoding="utf-8", errors="ignore"))
    number = str(meta.get("module") or path.stem.split("_", 1)[0]).zfill(2)
    title = str(meta.get("title") or path.stem.split("_", 1)[-1].replace("_", " "))
    return ModuleSummary(
        path.stem, number, title,
        str(meta.get("nav_label") or f"{number} · {title}"), path,
        nav_hidden=bool(meta.get("nav_hidden")),
    )


@lru_cache(maxsize=16)
def list_modules(pack_name: str = "datasphere_core") -> tuple[ModuleSummary, ...]:
    """Use the existing core registry as the content-pack source of truth."""
    return tuple(_summary(path) for path in iter_content_module_paths(pack_name))


@lru_cache(maxsize=16)
def list_appendices(pack_name: str = "datasphere_core") -> tuple[ModuleSummary, ...]:
    """Apéndices técnicos del pack (content/apendices/*.md), cada uno con su markdown."""
    return tuple(_summary(path) for path in iter_content_appendix_paths(pack_name))


@lru_cache(maxsize=16)
def list_academy(pack_name: str = "datasphere_core") -> tuple[ModuleSummary, ...]:
    """Páginas de Danwor Academy (content/academy/*.md). Solo metadatos de nav:
    cada una envuelve un HTML autónomo (frontmatter academy_asset)."""
    return tuple(_summary(path) for path in iter_content_academy_paths(pack_name))


@lru_cache(maxsize=64)
def academy_asset_for(stem: str, pack_name: str = "datasphere_core") -> str:
    """Nombre del HTML de storytelling asociado a una página de Academy.

    Se lee del frontmatter `academy_asset`. Devuelve "" si el stem no es una
    página de Academy válida o no declara asset.
    """
    summary = next((item for item in list_academy(pack_name) if item.stem == stem), None)
    if summary is None:
        return ""
    meta, _ = split_front_matter(summary.path.read_text(encoding="utf-8", errors="ignore"))
    asset = str(meta.get("academy_asset") or "").strip()
    # Defensa: solo un nombre de archivo simple (sin rutas ni traversal).
    if not asset or "/" in asset or "\\" in asset or ".." in asset:
        return ""
    return asset


def _section_base(stem: str, pack_name: str) -> int:
    total = 0
    for module in list_modules(pack_name):
        if module.stem == stem:
            break
        _, body = split_front_matter(module.path.read_text(encoding="utf-8", errors="ignore"))
        total += len(H2_RE.findall(body))
    return total


def _styled_title(raw: str) -> tuple[str, str]:
    plain = re.sub(r"^\[\d+\]\s*", "", TONE_RE.sub("", raw)).strip()
    parts, active, position = [], "", 0
    for match in TONE_RE.finditer(raw):
        chunk = raw[position:match.start()]
        if chunk:
            cls = f' class="tone-{active}"' if active else ""
            parts.append(f"<span{cls}>{html.escape(chunk)}</span>")
        active, position = match.group(1).lower(), match.end()
    cls = f' class="tone-{active}"' if active else ""
    parts.append(f"<span{cls}>{html.escape(raw[position:])}</span>")
    return plain, "".join(parts)


def _asset_relative_path(raw_path: str) -> str:
    parts = [part for part in raw_path.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    try:
        parts = parts[parts.index("assets") + 1:]
    except ValueError:
        pass
    return "/".join(parts)


def _support_links_html(raw_params: str, *, heading: bool = False) -> str:
    links = [
        f'<a class="support-link" href="{html.escape(url, quote=True)}" target="_blank" '
        f'rel="noopener">{html.escape(label.strip())}<span>↗</span></a>'
        for label, url in SUPPORT_ITEM_RE.findall(raw_params or "")
    ]
    if not links:
        return ""
    classes = "support-links heading-supports" if heading else "support-links"
    return f'<div class="{classes}">' + "".join(links) + "</div>"


def _extract_heading_supports(source: str) -> tuple[str, str]:
    """Extract only support controls placed at the beginning of a section."""
    remaining = SEMANTIC_RE.sub("", META_RE.sub("", source)).lstrip()
    support_groups = []
    while match := LEADING_SUPPORT_RE.match(remaining):
        token = SUPPORT_RE.fullmatch(match.group(1).strip())
        if token is None:
            break
        support_groups.append(token.group(1))
        remaining = remaining[match.end():]
    raw_params = " ".join(support_groups)
    return remaining, _support_links_html(raw_params, heading=True)



def _sap_note_table_links_html(source: str) -> str:
    """Linkea numeros de nota en tablas cuya primera columna es `Nota`.

    El markdown conserva solo el numero; el patron oficial de SAP Notes es
    estable y evita duplicar URLs en el contenido curado.
    """
    lines = (source or "").splitlines()
    output: list[str] = []
    in_note_table = False

    for line in lines:
        stripped = line.strip()
        is_table_line = stripped.startswith("|") and stripped.endswith("|")
        if not is_table_line:
            in_note_table = False
            output.append(line)
            continue

        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        first_cell = cells[0] if cells else ""
        first_plain = _norm(re.sub(r"<[^>]+>|[*_`\[\]()]", " ", first_cell))
        if first_plain in {"nota", "nota sap", "sap note"}:
            in_note_table = True
            output.append(line)
            continue

        if in_note_table and re.fullmatch(r":?-{3,}:?", first_cell):
            output.append(line)
            continue

        if in_note_table and cells:
            note_match = re.fullmatch(r"(?:\*\*)?(\d{6,10})(?:\*\*)?", first_cell)
            if note_match and "me.sap.com/notes" not in first_cell:
                note = note_match.group(1)
                linked = (
                    f'<a class="sap-note-link" href="https://me.sap.com/notes/{note}" '
                    f'target="_blank" rel="noopener noreferrer">{note}</a>'
                )
                cells[0] = linked
                line = "| " + " | ".join(cells) + " |"
        output.append(line)

    return "\n".join(output)


def _pipe_links_html(source: str) -> str:
    """Convierte `Texto | [Link](url)` en un link de texto completo.

    Se usa para la biblioteca de referencias: el texto queda visualmente como
    contenido normal, abre en una nueva pestana y solo revela affordance en hover.
    """
    def repl(match: re.Match[str]) -> str:
        prefix = match.group(1) or ""
        label = (match.group(2) or "").strip()
        url = (match.group(3) or "").strip()
        safe_label = html.escape(label)
        safe_url = html.escape(url, quote=True)
        return (
            f'{prefix}<a class="reference-text-link" href="{safe_url}" '
            f'target="_blank" rel="noopener noreferrer">{safe_label}</a>'
        )

    return PIPE_LINK_RE.sub(repl, source or "")


def _callout_inline_html(text: str) -> str:
    parts, position = [], 0

    def basic(chunk: str) -> str:
        safe = html.escape(chunk or "")
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)

    for match in CUSTOM_LINK_RE.finditer(text or ""):
        parts.append(basic(text[position:match.start()]))
        url = html.escape(match.group(1).strip(), quote=True)
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f'{basic(match.group(2).strip())}</a>'
        )
        position = match.end()
    parts.append(basic((text or "")[position:]))
    return "".join(parts)


def _callout_body_html(body: str) -> str:
    parts, paragraph, bullets = [], [], []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(line.strip() for line in paragraph if line.strip()).strip()
            if text:
                parts.append(f"<p>{_callout_inline_html(text)}</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li>{_callout_inline_html(item)}</li>" for item in bullets)
            parts.append(f'<ul class="callout-list">{items}</ul>')
            bullets.clear()

    for line in (body or "").strip().splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_bullets()
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            bullets.append(bullet.group(1).strip())
        else:
            flush_bullets()
            paragraph.append(stripped)
    flush_paragraph()
    flush_bullets()
    return "".join(parts)


def _prepare(source: str, pack_name: str, stem: str = "") -> str:
    source = SEMANTIC_RE.sub("", META_RE.sub("", source))

    def callout(match: re.Match[str]) -> str:
        attrs = {item.group(1).lower(): item.group(2) for item in CALLOUT_ATTR_RE.finditer(match.group(1))}
        tone = attrs.get("tone", "note").strip().lower()
        tone = {
            "green": "success", "verde": "success",
            "yellow": "punch", "amarillo": "punch",
            "red": "warn", "rojo": "warn", "warning": "warn", "danger": "warn",
        }.get(tone, tone)
        if tone not in {"note", "success", "punch", "warn"}:
            tone = "note"
        title = html.escape(attrs.get("title") or "Nota metodológica")
        icon = html.escape(attrs.get("icon") or "")
        return (
            f'<aside class="callout callout--{tone}">'
            f'<div class="callout__head"><span class="callout__icon">{icon}</span>'
            f'<strong class="callout__title">{title}</strong></div>'
            f'<div class="callout__body">{_callout_body_html(match.group(2))}</div>'
            f'</aside>'
        )

    def callout_row(match: re.Match[str]) -> str:
        return f'<div class="callout-row">{match.group(1).strip()}</div>'

    def embed(match: re.Match[str]) -> str:
        asset_path = _asset_relative_path(match.group(1))
        params = {}
        for item in EMBED_PARAM_RE.finditer(match.group(2) or ""):
            params[item.group(1).lower().replace("-", "_")] = next(
                (value for value in item.groups()[1:] if value is not None), ""
            )
        try:
            height = max(240, min(int(params.pop("height", "620")), 1600))
        except ValueError:
            height = 620
        query = f"?{urlencode(params)}" if params else ""
        label = Path(asset_path).stem.replace("danwor-", "").replace("-", " ").title()
        src = f"/pack-assets/{html.escape(pack_name, quote=True)}/{html.escape(asset_path, quote=True)}{html.escape(query, quote=True)}"
        return (
            f'<div class="html-embed-shell" style="--embed-height:{height}px">'
            f'<iframe class="content-embed" src="{src}" title="{html.escape(label, quote=True)}" '
            f'loading="lazy"></iframe></div>'
        )

    def support(match: re.Match[str]) -> str:
        return _support_links_html(match.group(1))

    def tree_button(match: re.Match[str]) -> str:
        # Soporta dos formas:
        #   {{tree_button: Opciones de Extracción}}
        #   {{tree_button: id=extraction_options label="Opciones de Extracción"}}
        # La segunda permite reutilizar árboles de otro módulo sin duplicar YAML.
        raw = match.group(1).strip()
        params = {}
        if "=" in raw:
            for item in EMBED_PARAM_RE.finditer(raw):
                params[item.group(1).lower().replace("-", "_")] = next(
                    (value for value in item.groups()[1:] if value is not None), ""
                )
        tree_id = str(params.get("id") or params.get("tree_id") or "").strip()
        title = str(params.get("label") or params.get("title") or raw).strip()
        payload = html.escape(
            json.dumps({"tree_id": tree_id, "tree_title": title, "pack": pack_name}, ensure_ascii=False), quote=True
        )
        # Se rodea el bot?n con l?neas en blanco y se envuelve en un <div> block-level:
        # as? markdown nunca lo pega al p?rrafo anterior (quedar?a inline dentro de un
        # <p> y no tomar?a el ancho completo ? bug del "bot?n cuadrado").
        return (
            f'\n\n<div class="tree-launch-wrap">'
            f'<button type="button" class="tree-launch" '
            f'hx-post="/modules/{html.escape(stem, quote=True)}/trees/start" '
            f'hx-target="#tree-tab-panel" hx-swap="outerHTML" hx-vals="{payload}" '
            f'@click="toolTab=&#39;tree&#39;; panelOpen=true">'
            f'<span class="tree-launch-eyebrow">Árbol de decisión</span>'
            f'<span class="tree-launch-title">{html.escape(title)}</span>'
            f'<span class="tree-launch-cta">Hacé click para abrir en panel de herramientas de decisión</span>'
            f'</button></div>\n\n'
        )

    rendered_callouts = CALLOUT_RE.sub(callout, source)
    rendered_callouts = CALLOUT_ROW_RE.sub(callout_row, rendered_callouts)
    rendered_tokens = SUPPORT_RE.sub(support, EMBED_RE.sub(embed, rendered_callouts))
    rendered_tokens = TREE_BUTTON_RE.sub(tree_button, rendered_tokens)
    rendered_tokens = _pipe_links_html(rendered_tokens)
    rendered_tokens = _sap_note_table_links_html(rendered_tokens)
    # Cualquier token de línea restante es metadata/control editorial. Nunca
    # debe llegar como texto a la vista web (callout_row, etc.).
    return INTERNAL_TOKEN_LINE_RE.sub("", rendered_tokens)


@lru_cache(maxsize=64)
def load_module(stem: str, pack_name: str = "datasphere_core") -> ModuleDocument:
    """Parsea y renderiza un módulo. Cacheado: el resultado es inmutable
    (ModuleDocument frozen + tuplas) y solo depende de (stem, pack) y archivos
    estáticos, así que evita re-leer y re-renderizar markdown en cada request."""
    summary = next((item for item in list_modules(pack_name) if item.stem == stem), None)
    if summary is None:
        path = resolve_content_path(stem, pack_name)
        if not path.exists():
            raise FileNotFoundError(stem)
        summary = _summary(path)
    source = summary.path.read_text(encoding="utf-8", errors="ignore")
    meta, body = split_front_matter(source)
    headings, base, sections = list(H2_RE.finditer(body)), _section_base(stem, pack_name), []
    for offset, match in enumerate(headings):
        end = headings[offset + 1].start() if offset + 1 < len(headings) else len(body)
        title, styled = _styled_title(match.group(1))
        section_markdown = body[match.start():end].strip()
        section_body, heading_supports = _extract_heading_supports(body[match.end():end].strip())
        prepared = _prepare(section_body, pack_name, stem)
        has_subtitle = prepared.lstrip().startswith(">")
        rendered = markdown.markdown(prepared, extensions=["tables", "fenced_code", "sane_lists"])
        if has_subtitle:
            rendered = rendered.replace("<blockquote>", '<blockquote class="section-subtitle">', 1)
        number = base + offset + 1
        subtitle_class = " has-subtitle" if has_subtitle else ""
        rendered = (
            f'<header class="content-heading{subtitle_class}">'
            f'<div class="content-heading-copy"><span class="section-eyebrow">[{number:02d}]</span>'
            f'<h2>{styled}</h2></div>{heading_supports}</header>{rendered}'
        )
        sections.append(ModuleSection(number, title, slugify(title), rendered, section_markdown))
    return ModuleDocument(
        summary,
        str(meta.get("scope") or "").strip(),
        tuple(map(str, meta.get("guardrails") or ())),
        tuple(sections),
        source,
    )
