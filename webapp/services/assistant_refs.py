"""Referencias navegables INLINE en las respuestas del Asistente (contrato estructurado, sin coincidencias textuales).

El Asistente distingue tres naturalezas de interacción:
  · acciones de continuidad conversacional («Explicame vos», abrir el Catálogo, ir a otro módulo) y
  · alternativas de decisión / desambiguación (`ask`)  → botones contextuales debajo de la burbuja;
  · referencias a OBJETOS MENCIONADOS en la respuesta (una CDS view, un modelo del Catálogo, una conexión, un recurso, una sección,
    un Informe o un Assessment) → link inline sobre el propio nombre, sin repetirlo como botón.

El productor de la respuesta (router determinístico) marca cada mención con el ÍNDICE de su destino ya resuelto, al armar el texto:
nunca se busca el nombre en el texto después, ni el LLM inventa URLs (sus respuestas no traen referencias). La marca usa caracteres de
uso privado de Unicode, que sobreviven intactos al render markdown; `plain` las quita (texto que se persiste, se manda como historial al
LLM y se lee en métricas) y `resolve` las convierte en el link del destino (solo en la respuesta en vivo).
"""
from __future__ import annotations

import re
from typing import Callable, Generic, TypeVar

OPEN, SEP, CLOSE = "", "", ""
_MARK = re.compile(re.escape(OPEN) + r"(\d+)" + re.escape(SEP) + r"(.*?)" + re.escape(CLOSE), re.S)
_STRAY = re.compile("[" + OPEN + SEP + CLOSE + "]")

T = TypeVar("T")


class References(Generic[T]):
    """Acumula los destinos de las menciones de UNA respuesta; llamarla con (texto, destino) devuelve el texto marcado."""

    def __init__(self) -> None:
        self.items: list[T] = []

    def __call__(self, text: str, target: T) -> str:
        self.items.append(target)
        return f"{OPEN}{len(self.items) - 1}{SEP}{_STRAY.sub('', str(text))}{CLOSE}"

    def tuple(self) -> tuple[T, ...]:
        return tuple(self.items)


def plain(text: str) -> str:
    """Texto sin marcas (el nombre queda tal cual)."""
    return _STRAY.sub("", _MARK.sub(lambda m: m.group(2), text or ""))


def resolve(rendered_html: str, link: Callable[[int, str], str]) -> str:
    """Reemplaza cada marca del HTML ya renderizado por ``link(índice, html_interno)``; lo que quede suelto se descarta."""
    return _STRAY.sub("", _MARK.sub(lambda m: link(int(m.group(1)), m.group(2)), rendered_html or ""))
