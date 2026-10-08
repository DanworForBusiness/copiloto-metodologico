"""Mapeo canónico ÚNICO: estado semántico → tono del badge del Hub.

Regla (decisión del owner, Gate UX.4): el COLOR representa el ESTADO SEMÁNTICO (valencia), **no** el rango ordinal.
«Buena» es favorable en todos lados → verde; «Plena» comparte el tono positivo y se distingue por la ETIQUETA, no por el
color. Las advertencias (p. ej. "revisar origen") se muestran con un indicador de atención APARTE, nunca recoloreando el
estado. Esta es la ÚNICA fuente de verdad; Catálogo (veredicto), Adopción metodológica y los informes la consumen. Cambiar
el mapeo acá actualiza a todos los consumidores.

Los tonos son los modificadores del badge del Hub `.catalog-card__verdict--{tone}` (y `.catalog-card--{tone}` / `.catalog-meta-dot--{tone}`):
    plena   = verde    → POSITIVO / favorable   (Plena, Buena)
    buena   = ámbar    → ACEPTABLE con reparos   (Adecuada)
    parcial = naranja  → REQUIERE ATENCIÓN       (Parcial)
    neutra  = gris     → NEUTRO / no determinable (No determinable, sin dictamen)
"""
from __future__ import annotations

import re
import unicodedata

# Tonos canónicos (nombres de las clases `.catalog-card__verdict--*`).
POSITIVE = "plena"
ACCEPTABLE = "buena"
ATTENTION = "parcial"
NEUTRAL = "neutra"

# Estado semántico (normalizado, sin acentos/espacios) → tono por VALENCIA.
_STATUS_TONE = {
    "plena": POSITIVE,
    "buena": POSITIVE,            # favorable → verde (NO ámbar): el color es el estado, no el rango
    "adecuada": ACCEPTABLE,       # aceptable con oportunidades → ámbar
    "parcial": ATTENTION,         # requiere atención → naranja
    "nodeterminable": NEUTRAL,
    "sindictamen": NEUTRAL,
    "neutra": NEUTRAL,
}


def normalize_status(value: str) -> str:
    """Minúsculas sin acentos ni separadores: 'No determinable' → 'nodeterminable', 'Buena' → 'buena'."""
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def status_tone(value: str) -> str:
    """Tono canónico del Hub para un estado semántico. Default NEUTRAL para estados desconocidos."""
    return _STATUS_TONE.get(normalize_status(value), NEUTRAL)
