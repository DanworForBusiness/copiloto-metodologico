"""
catalog_cds_docs.py — resolvedor ÚNICO de la documentación de una CDS view (``APIs/<cds>.md``) a partir del NOMBRE con que el Catálogo la conoce.

Dos identidades distintas pueden nombrar la MISMA CDS view:
  · el nombre de la ENTIDAD (``define view entity ZCDS_DSP_DIM_ADRC``): el documento se llama igual (``APIs/ZCDS_DSP_DIM_ADRC.md``);
  · el nombre de su VISTA SQL (``@AbapCatalog.sqlViewName: 'ZCDS_BILLING'``): es el nombre con el que ODP/Datasphere importa la tabla remota
    (``ABAP_CDS.ZCDS_BILLING$F``) aunque el documento se llame como la entidad (``ZCDS_DSP_BILLING.md``).

Reglas, todas sobre metadata EXPLÍCITA (nunca similitud de nombres ni prefijos); una contradicción explícita SIEMPRE gana sobre la coincidencia de nombre de archivo:
  1. existe ``APIs/<nombre>.md`` Y ningún otro documento declara ese nombre como su ``sqlViewName`` Y el propio documento no declara OTRO ``sqlViewName`` → ese;
  2. no existe ese archivo y exactamente UN documento declara ``@AbapCatalog.sqlViewName`` igual (sin distinguir mayúsculas) al nombre → ese;
  3. cualquier otro caso (sin documento, varios candidatos, o metadata que contradice la coincidencia de nombre) → ``""``: no hay correspondencia confiable y NO se muestra botón.
Solo lectura; el índice se recalcula si cambia el contenido de la carpeta (nombre, tamaño o mtime de algún ``.md``).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

_SQL_VIEW = re.compile(r"@AbapCatalog\.sqlViewName\s*:\s*['\"]([^'\"]+)['\"]", re.I)
_lock = threading.Lock()
_memo: dict[str, tuple[tuple, tuple[dict[str, list[str]], dict[str, set[str]]]]] = {}


def _index(apis_dir: Path) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """(``{SQLVIEWNAME → [stems que lo declaran]}``, ``{stem → {SQLVIEWNAME declarados}}``) de ``apis_dir``. Vacío si la carpeta no existe."""
    try:
        files = sorted(p for p in apis_dir.glob("*.md") if p.is_file())
    except OSError:
        return {}, {}
    sig = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files)
    key = str(apis_dir)
    with _lock:
        cached = _memo.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
    by_sql: dict[str, list[str]] = {}
    by_stem: dict[str, set[str]] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            by_stem[p.stem] = set()
            continue
        declared = {m.upper() for m in _SQL_VIEW.findall(text)}
        by_stem[p.stem] = declared
        for name in declared:
            by_sql.setdefault(name, []).append(p.stem)
    with _lock:
        _memo[key] = (sig, (by_sql, by_stem))
    return by_sql, by_stem


def resolve(apis_dir: Path, name: str) -> str:
    """Stem del documento ``APIs/<stem>.md`` de la CDS view conocida como ``name`` (entidad o vista SQL); ``""`` si no hay correspondencia confiable."""
    name = str(name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return ""
    by_sql, by_stem = _index(apis_dir)
    up = name.upper()
    claimed_by = by_sql.get(up, [])
    if (apis_dir / f"{name}.md").exists():
        own = by_stem.get(name, set())
        if any(stem != name for stem in claimed_by):
            return ""                                                     # otro documento declara este nombre como su vista SQL: la coincidencia de archivo queda contradicha
        if own and up not in own:
            return ""                                                     # el propio documento declara OTRA vista SQL: no es el que ODP importa con este nombre
        return name                                                       # 1. identidad explícita sin contradicciones
    return claimed_by[0] if len(claimed_by) == 1 else ""                  # 2. sqlViewName declarado por UN solo documento; 3. nada o ambiguo → sin botón
