"""
utils.py — Utilidades compartidas del Copiloto Metodológico Danwor.

Centraliza funciones de uso transversal para evitar duplicación y
asegurar comportamiento consistente en todo el codebase.
"""

import re
import unicodedata


def norm_text(s: str) -> str:
    """
    Normaliza un string para comparaciones robustas:
      - Convierte a minúsculas
      - Elimina tildes y diacríticos (á→a, é→e, ü→u, ñ→n, etc.)

    Usar siempre que se comparen strings ingresados por el usuario
    contra strings definidos en YAMLs, .md u otras fuentes de datos.

    Ejemplo:
        norm_text("Código")  == norm_text("codigo")  # True
        norm_text("extracción") == norm_text("extraccion")  # True
    """
    return ''.join(
        c for c in unicodedata.normalize('NFD', s.lower())
        if unicodedata.category(c) != 'Mn'
    )


def slugify(text: str) -> str:
    """
    Convierte un heading o título en slug URL-safe estable para navegación.

    Fuente de verdad única — usar en app.py, content.py y tools_panel.py.
    Maneja emojis, prefijos numéricos [N] (conservando el número), caracteres
    especiales y diacríticos.

    Ejemplo:
        slugify("[18] ✔️ Clean Core aplicado")  →  "18-clean-core-aplicado"
        slugify("¿Qué es ODP?")                 →  "que-es-odp"
    """
    text = re.sub(r"\{dw-(?:black|gold|muted|accent|danger)\}", "", text or "", flags=re.I)
    s = norm_text(text or "")
    # Normalizar prefijos numéricos [18] → "18 " (conservar el número)
    s = re.sub(r"^\[?(\d+)\]?\s*", r"\1 ", s)
    # Eliminar todo lo que no sea alfanumérico, espacio o guión
    s = re.sub(r"[^a-z0-9\s\-_]", "", s)
    # Colapsar espacios en guiones
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "section"
