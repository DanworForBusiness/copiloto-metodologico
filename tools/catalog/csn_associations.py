"""Extracción canónica de ASOCIACIONES CSN (`cds.Association`) a partir de metadata ESTRUCTURAL del modelo.

Helper canónico (lo consume el walk de linaje del extractor ``DSP_CLI_Export_Lineage``). Codifica la MISMA regla
estructural que ya aplica el generador de catálogo (``generate_catalog._extract_elements``, módulo legacy congelado por
un guard de aislamiento: por eso no se importa desde acá, para no tocarlo). NO usa regex, nombres de negocio ni
heurística: lee ``definitions[...].elements`` y reconoce un elemento de asociación por su ``type`` (termina en
"Association"), cuyo ``target`` es la entidad asociada. Vale por igual para vistas SQL y graphical (ambas exponen
``elements`` en el CSN) y para Analytic Models (que también declaran asociaciones en ``elements``).
"""
from __future__ import annotations

from typing import Any, Mapping


def is_association_element(element: Any) -> bool:
    """¿El elemento CSN es una asociación (``cds.Association`` u otra ``*Association``)? Regla estructural, sin heurística."""
    return isinstance(element, Mapping) and str(element.get("type", "")).endswith("Association")


def association_targets(defn: Any) -> set[str]:
    """Nombres técnicos de las entidades ASOCIADAS declaradas en ``defn.elements`` (el ``target`` de cada asociación).
    Devuelve un conjunto vacío si ``defn`` no es un mapping o no tiene ``elements``. Solo targets declarados y no vacíos:
    nunca se infiere un target de una etiqueta ni se fabrica un nombre."""
    elems = defn.get("elements") if isinstance(defn, Mapping) else None
    if not isinstance(elems, Mapping):
        return set()
    out: set[str] = set()
    for element in elems.values():
        if is_association_element(element):
            target = element.get("target")
            if isinstance(target, str) and target:
                out.add(target)
    return out
