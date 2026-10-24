"""search_text.py — normalización ESPECÍFICA de búsqueda libre de Capital Humano (insensible a mayúsculas, minúsculas y diacríticos).

Es SOLO para comparar texto tipeado contra texto mostrado en un buscador: nunca para identidad. `human_capital_service._normalize_text` (minúsculas + espacios; claves de duplicados e igualdad exacta de filtros/catálogos) NO se toca ni se reemplaza.
`n`/`ñ` son equivalentes solo acá. No cambia ningún valor persistido ni cómo se muestra.

CONTRATO COMPARTIDO con el navegador (`dwSearchFold` en `webapp/static/app.js`, que filtra en vivo el selector «Agregar candidato desde el Banco»): mismos pasos y MISMO orden —descomponer (NFD), quitar marcas
combinantes U+0300–U+036F, pasar a minúsculas, colapsar espacios—. Un test corre ambas implementaciones sobre el mismo corpus y exige resultados idénticos."""
from __future__ import annotations

import re
import unicodedata

_MARKS = re.compile("[̀-ͯ]")


def fold(value) -> str:
    """`Díaz` / `DIAZ` / `  díaz ` -> `diaz`; `Ñandú` -> `nandu`. `None`/`""` -> `""`."""
    if not value:
        return ""
    return " ".join(_MARKS.sub("", unicodedata.normalize("NFD", str(value))).lower().split())
