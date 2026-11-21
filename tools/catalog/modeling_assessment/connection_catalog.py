"""connection_catalog.py — lectura MINIMA y PURA del Catalogo de Conexiones (fichas ``Conexiones/conn_space_*.json``) para propagar el ``typeId`` del conector
al clasificador de contrato de fuente (``facts_field_lineage.source_contract_class_facts``).

Principios (decisiones del owner, 2026-11-21):
  * SCOPEADO AL CLIENTE: solo se leen las fichas de ``<inbound>/<cliente>/Conexiones``. Una conexion de otro cliente NUNCA puede acreditar una hoja (imposible por construccion).
  * ABSTENCION ANTE AMBIGÜEDAD: si un mismo ``name`` resuelve a ``typeId`` DISTINTO entre fichas (p.ej. mismo nombre en dos espacios), el nombre se OMITE del mapa; la hoja queda indeterminada.
  * EVIDENCIA EXPLICITA: se lee el ``typeId`` declarado en la ficha; no se infiere por nombres, prefijos, URLs ni similitud. La coincidencia de ``typeId`` acredita el MECANISMO de extraccion,
    NUNCA la identidad fisica de la instancia ni que la conexion este operativamente vigente.
  * TOLERANTE: sin carpeta ``Conexiones/`` (p.ej. un pool subido sin el catalogo) devuelve ``{}`` -> ninguna hoja se reconoce por conexion. Nunca lanza, nunca escribe, nunca toca produccion.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONEXIONES_DIR = "Conexiones"
_FICHA_GLOB = "conn_space_*.json"
_AMBIGUOUS = object()   # centinela interno: un mismo nombre resolvio a mas de un typeId dentro del cliente -> se descarta (abstencion)


def _safe_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def connection_types(inbound: Path, client_dir: str) -> dict[str, str]:
    """``{nombre de conexion -> typeId}`` INEQUIVOCO del Catalogo de Conexiones del cliente. Un ``name`` con ``typeId`` divergente entre fichas se OMITE (abstencion).
    Vacio si no existe la carpeta ``Conexiones/``. El resultado YA esta scopeado al cliente; el consumidor solo debe resolver por nombre exacto."""
    base = Path(inbound) / client_dir / _CONEXIONES_DIR
    if not base.is_dir():
        return {}
    acc: dict[str, Any] = {}
    for f in sorted(base.glob(_FICHA_GLOB)):
        doc = _safe_json(f)
        for it in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(it, dict):
                continue
            name, type_id = it.get("name"), it.get("typeId")
            if not isinstance(name, str) or not name or not isinstance(type_id, str) or not type_id:
                continue
            prev = acc.get(name)
            if prev is None:
                acc[name] = type_id
            elif prev is not _AMBIGUOUS and prev != type_id:
                acc[name] = _AMBIGUOUS                      # divergencia de typeId para el mismo nombre -> abstencion
    return {n: t for n, t in acc.items() if t is not _AMBIGUOUS}
