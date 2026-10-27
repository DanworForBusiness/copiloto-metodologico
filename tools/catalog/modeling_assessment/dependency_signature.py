"""dependency_signature.py — firma de DEPENDENCIAS de un assessment (granularidad de la vigencia, P1).

La vigencia del assessment compara la firma del POOL COMPLETO del cliente: publicar un modelo cambia esa firma y dejaba ``STALE`` a todos los demás assessments del cliente aunque
ninguna de sus entradas hubiera cambiado. Esta firma cubre SOLO lo que el assessment de un AM puede leer, para poder demostrar independencia:

  D0  los archivos de las entidades de la clausura semántica del AM (más la AUSENCIA explícita de un archivo cuando la clausura nombra un objeto que el pool no tiene);
  D1  todo archivo del pool que NO es una entidad del grafo (flujos de replicación/transformación, tareas, etc.) y MENCIONA a un objeto de la clausura;
  D2  los archivos que DEFINEN los objetos mencionados por los de D1 (orígenes y destinos de esos flujos);
  +   la CONTENCIÓN: qué AM terminales contienen cada artefacto de la clausura (el motor deriva reutilización entre AM de ese hecho).
Las entidades que CONSUMEN a la clausura sin pertenecer a ella (otro AM sobre la misma dimensión) no se arrastran por mención: su efecto sobre este AM es la contención, que ya está en la firma.
Los sidecars ``.txt`` de una entidad (mismo nombre) pertenecen a esa entidad.

Es una cota SUPERIOR conservadora (una mención textual cuenta, aunque no sea una referencia estructural): puede declarar «afectado» algo que no lo era, nunca al revés. Cuando no se
puede asegurar la dependencia (un archivo no se puede leer, el mismo nombre de archivo existe en dos carpetas) la firma es ``None`` = INCIERTO y el llamador cae a la firma del pool.
No toca reglas metodológicas ni el perfil: es un dato de vigencia aparte, guardado en el sobre.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import legacy_bridge as bridge

DEPENDENCY_RULE_VERSION = "dep-closure/v1"

_TOKEN = re.compile(r"[A-Za-z0-9_$./:-]+")
_WORD = re.compile(r"[A-Za-z0-9_$]+")


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text)) | frozenset(_WORD.findall(text))


def dependency_signatures(root: Path, group: Mapping[str, list[Path]], entities: Mapping[str, Any], universe: Any) -> dict[str, str | None]:
    """``{AM terminal: firma | None}`` para TODOS los AM terminales del universo, en UNA pasada sobre el pool (lee cada archivo una vez).
    ``entities``/``universe``: los MISMOS que usa el motor (``shadow.semantic_universe``). ``None`` = dependencias inciertas."""
    files = [*group["jsons"], *group["txts"]]
    rel = {p: p.relative_to(root).as_posix() for p in files}
    tokens: dict[Path, frozenset[str]] = {}
    sha: dict[Path, str] = {}
    unreadable = False
    for p in files:
        try:
            tokens[p] = _tokens(p.read_text(encoding="utf-8", errors="replace"))
            sha[p] = bridge.file_sha256(p)
        except OSError:
            unreadable = True
    by_basename: dict[str, list[Path]] = {}
    for p in files:
        by_basename.setdefault(p.name, []).append(p)
    defined: dict[Path, set[str]] = {p: {p.stem} for p in files}
    entity_file: dict[str, Path | None] = {}
    owned: dict[Path, str] = {}                              # archivo → entidad a la que pertenece (su CSN y sus sidecars de texto)
    for name, ent in entities.items():
        base = str(getattr(ent, "file", "") or "")
        hits = by_basename.get(base, [])
        entity_file[name] = hits[0] if len(hits) == 1 else None
        if len(hits) == 1:
            defined[hits[0]].add(name)
            owned[hits[0]] = name
    for p in group["txts"]:
        if p.stem in entities and p not in owned:
            owned[p] = p.stem
    out: dict[str, str | None] = {}
    for am in universe.terminal_ams:
        out[am] = None if unreadable else _signature_for(am, universe, entity_file, owned, tokens, sha, rel, defined, files, entities)
    return out


def _signature_for(am, universe, entity_file, owned, tokens, sha, rel, defined, files, entities) -> str | None:
    closure = tuple(universe.closure_of(am))
    names = set(closure)
    d0: set[Path] = set()
    closure_files: list[tuple[str, str]] = []
    for name in closure:
        if name not in entities:
            closure_files.append((name, "-"))              # el pool NO tiene ese objeto: su aparición posterior cambia la firma
            continue
        path = entity_file.get(name)
        if path is None:
            return None                                     # el archivo de la entidad no es unívoco: dependencia incierta
        d0.add(path)
        closure_files.append((name, rel[path]))
    d0 |= {p for p, owner in owned.items() if owner in names}                       # sidecars de texto de las entidades de la clausura
    d1 = {p for p in files if p not in d0 and p not in owned and p in tokens and tokens[p] & names}
    mentioned: set[str] = set()
    for p in d1:
        mentioned |= tokens[p]
    d2 = {p for p in files if p not in d0 and p not in d1 and defined[p] & mentioned}
    contained = {name: list(universe.terminal_ams_containing(name)) for name in closure}
    payload = {"rule": DEPENDENCY_RULE_VERSION, "am": am, "closure": closure_files,
               "files": sorted((rel[p], sha[p]) for p in (d0 | d1 | d2) if p in sha), "containment": contained}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
