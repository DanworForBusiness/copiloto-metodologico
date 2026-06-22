"""
corpus.py — Índice cacheado del corpus de contenido.

Problema que resuelve: el router reparsea TODO el corpus en cada prompt
(split_front_matter + parse_section_meta + extract_section_by_heading por cada
sección de cada módulo). El parseo no depende de la pregunta, solo del contenido
en disco, así que se puede cachear e invalidar por mtime.

`get_corpus_index()` devuelve las secciones ya parseadas. El scoring por pregunta
sigue ocurriendo en el consumidor (eso sí depende del prompt y no se cachea).

Contrato: el índice produce exactamente los mismos datos que el parseo inline
que reemplaza. La red de caracterización en tests/ lo verifica.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from core.content_metadata import (
    split_front_matter,
    parse_section_meta,
    extract_section_by_heading,
    module_label_from_md,
)
from core.content_registry import iter_content_markdown_paths

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SectionEntry:
    """Una sección con section_meta, ya parseada."""

    module: str                  # stem del módulo
    module_label: str
    path: str
    scope: str                   # frontmatter `scope` del módulo
    heading: str
    keywords: tuple[str, ...]
    section_md: str


@dataclass(frozen=True)
class ModuleEntry:
    stem: str
    path: str
    module_label: str
    scope: str
    sections: tuple[SectionEntry, ...]


def _fingerprint(pack_name: str | None = None) -> tuple[tuple[str, float], ...]:
    """Clave de cache: (ruta, mtime) por archivo. Invalida solo al cambiar contenido."""
    entries: list[tuple[str, float]] = []
    for path in iter_content_markdown_paths(pack_name):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append((str(path), mtime))
    return tuple(entries)


@lru_cache(maxsize=16)
def _build_index(
    pack_name: str | None,
    fingerprint: tuple[tuple[str, float], ...],
) -> tuple[ModuleEntry, ...]:
    modules: list[ModuleEntry] = []
    for path_str, _mtime in fingerprint:
        path = Path(path_str)
        try:
            md = path.read_text(encoding="utf-8")
            meta, body = split_front_matter(md)
            sections = parse_section_meta(body)
            scope = str(meta.get("scope") or "")
            label = module_label_from_md(path.stem, md)
            section_entries: list[SectionEntry] = []
            for section in sections:
                heading = section.get("heading", "")
                if not heading:
                    continue
                section_md = extract_section_by_heading(body, heading, level=(2, 3)) or ""
                section_entries.append(SectionEntry(
                    module=path.stem,
                    module_label=label,
                    path=str(path),
                    scope=scope,
                    heading=heading,
                    keywords=tuple(section.get("keywords") or []),
                    section_md=section_md,
                ))
        except Exception as exc:
            # Un archivo malformado se omite (igual que el parseo inline original).
            log.warning("No se pudo indexar %s, se omite: %s", path_str, exc)
            continue
        modules.append(ModuleEntry(path.stem, str(path), label, scope, tuple(section_entries)))
    return tuple(modules)


def get_corpus_index(pack_name: str | None = None) -> tuple[ModuleEntry, ...]:
    """Módulos del corpus activo, parseados y cacheados por mtime."""
    return _build_index(pack_name, _fingerprint(pack_name))
