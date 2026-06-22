"""Pure metadata parsers shared by routing and non-Streamlit frontends."""
from __future__ import annotations

from functools import lru_cache
import html
import logging
from pathlib import Path
import re
from typing import Iterable

import yaml

from core.utils import norm_text as _norm

log = logging.getLogger(__name__)

SECTION_META_RE = re.compile(r"^\s*\{\{\s*section_meta\s*:\s*(.*?)\s*\}\}\s*$", re.I | re.M)
SECTION_META_KV_RE = re.compile(r'keywords\s*=\s*"([^"]+)"', re.I)
SECTION_EXCLUDE_RE = re.compile(r'^\s*\{\{\s*section_exclude\s*:\s*"(.*?)"\s*\}\}\s*$', re.I | re.M)
HEADING_TONE_RE = re.compile(r"\{([^{}]+)\}")


@lru_cache(maxsize=256)
def read_text_cached(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8", errors="ignore")


def split_front_matter(md: str) -> tuple[dict, str]:
    if md.startswith("---"):
        parts = md.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1].strip()) or {}
            except Exception as exc:
                log.warning("Frontmatter YAML invalido, se ignora: %s", exc)
                meta = {}
            return meta, parts[2].lstrip("\n")
    return {}, md


def parse_markdown_headings(md: str) -> list[tuple[int, str, int]]:
    return [
        (len(match.group(1)), match.group(2).strip(), match.start())
        for match in re.finditer(r"^(#{1,6})\s+(.+)$", md, flags=re.M)
    ]


def parse_section_meta(md_body: str) -> list[dict]:
    headings = parse_markdown_headings(md_body)
    results = []
    for match in SECTION_META_RE.finditer(md_body):
        values = SECTION_META_KV_RE.search(match.group(1))
        if not values:
            continue
        heading = next(
            (title for _level, title, position in reversed(headings) if position < match.start()),
            None,
        )
        keywords = [item.strip() for item in values.group(1).split(",") if item.strip()]
        if heading and keywords:
            results.append({"heading": heading, "keywords": keywords})
    return results


def parse_section_excludes(md_body: str) -> dict[str, list[str]]:
    headings = parse_markdown_headings(md_body)
    result: dict[str, list[str]] = {}
    for match in SECTION_EXCLUDE_RE.finditer(md_body):
        heading = next(
            (title for _level, title, position in reversed(headings) if position < match.start()),
            None,
        )
        excludes = [item.strip() for item in match.group(1).split(",") if item.strip()]
        if heading and excludes:
            result[heading] = excludes
    return result


def module_label_from_md(stem: str, md_text: str) -> str:
    meta, _ = split_front_matter(md_text)
    appendix = re.match(r"^(A\d{2})_(.+)$", stem, re.I)
    if appendix:
        return f"{appendix.group(1).upper()} · {appendix.group(2).replace('_', ' ')}"
    module = str(meta.get("module", "")).strip()
    title = str(meta.get("title", "")).strip()
    if module and title:
        return f"{module.zfill(2) if module.isdigit() else module} · {title}"
    fallback = re.match(r"^(\d{2})_(.+)$", stem)
    return f"{fallback.group(1)} · {fallback.group(2).replace('_', ' ')}" if fallback else stem


def _heading_key(text: str) -> str:
    text = html.unescape(text or "")
    text = HEADING_TONE_RE.sub("", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s*\[\d+\]\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", _norm(text))
    return re.sub(r"\s+", " ", text).strip()


def extract_section_by_heading(
    md_body: str, heading: str, level: int | tuple[int, ...] = 2,
) -> str | None:
    levels: Iterable[int] = (level,) if isinstance(level, int) else level
    target = _heading_key(heading)
    if not target:
        return None
    headings = parse_markdown_headings(md_body)
    for index, (candidate_level, title, start) in enumerate(headings):
        if candidate_level not in levels or _heading_key(title) != target:
            continue
        end = len(md_body)
        for next_level, _next_title, next_start in headings[index + 1:]:
            if next_level <= candidate_level:
                end = next_start
                break
        return md_body[start:end].strip()
    return None
