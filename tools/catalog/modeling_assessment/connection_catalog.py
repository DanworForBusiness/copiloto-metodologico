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


_INDEX_PREFIX = "conn_space_"


def spaces_of(inbound: Path, client_dir: str) -> list[str]:
    """Spaces del Catalogo de Conexiones del cliente, por su INDICE ``conn_space_<SPACE>.json`` (lista de conexiones del Space). El ``space_id`` de las
    fichas es un identificador interno (GUID), no el nombre del Space: la identidad del Space sale del nombre del indice."""
    base = Path(inbound) / client_dir / _CONEXIONES_DIR
    if not base.is_dir():
        return []
    out = []
    for f in sorted(base.glob(_FICHA_GLOB)):
        if isinstance(_safe_json(f), list):                                  # solo el INDICE del Space es una lista
            out.append(f.stem[len(_INDEX_PREFIX):])
    return out


def _space_of_file(f: Path, spaces: list[str]) -> str | None:
    """Space de una ficha por su nombre: el indice ``conn_space_<SPACE>.json`` o la ficha ``conn_space_<SPACE>_<CONEXION>.json`` de un Space CONOCIDO (por el
    indice). Si el prefijo calza con mas de un Space conocido (nombres con ``_``), se toma el MAS LARGO; sin Space conocido, None (no se atribuye)."""
    rest = f.stem[len(_INDEX_PREFIX):]
    if rest in spaces:
        return rest
    hits = [s for s in spaces if rest.startswith(s + "_")]
    return max(hits, key=len) if hits else None


def connection_records(inbound: Path, client_dir: str) -> list[dict]:
    """Fichas del cliente con su Space: ``[{space, name, typeId, file}]`` (una por conexion y Space; el indice y la ficha detallada se deduplican). Lectura pura."""
    base = Path(inbound) / client_dir / _CONEXIONES_DIR
    spaces = spaces_of(inbound, client_dir)
    seen, out = set(), []
    for f in sorted(base.glob(_FICHA_GLOB)) if base.is_dir() else []:
        space = _space_of_file(f, spaces)
        doc = _safe_json(f)
        for it in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not it.get("name"):
                continue
            key = (space, it["name"], it.get("typeId"))
            if key not in seen:
                seen.add(key)
                out.append({"space": space, "name": it["name"], "typeId": it.get("typeId"), "file": f.name})
    return out


def connection_types(inbound: Path, client_dir: str, spaces: Any = None) -> dict[str, str]:
    """``{nombre de conexion -> typeId}`` INEQUIVOCO del Catalogo de Conexiones del cliente. Un ``name`` con ``typeId`` divergente entre fichas se OMITE (abstencion).
    Vacio si no existe la carpeta ``Conexiones/``. El resultado YA esta scopeado al cliente; el consumidor solo debe resolver por nombre exacto.
    ``spaces`` (opcional): CONTEXTO DE ORIGEN AUTORIZADO — solo se leen las fichas de esos Spaces (por el nombre de archivo; una ficha cuyo Space no se puede
    atribuir queda FUERA). Sin ``spaces`` el comportamiento es el de siempre (todas las fichas del cliente): no hay restriccion global."""
    base = Path(inbound) / client_dir / _CONEXIONES_DIR
    if not base.is_dir():
        return {}
    allowed = None if spaces is None else {str(s) for s in spaces}
    known = spaces_of(inbound, client_dir) if allowed is not None else []
    acc: dict[str, Any] = {}
    for f in sorted(base.glob(_FICHA_GLOB)):
        if allowed is not None and _space_of_file(f, known) not in allowed:
            continue
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
