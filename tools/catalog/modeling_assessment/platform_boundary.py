"""platform_boundary.py — evidencia POSITIVA de fronteras de plataforma (``platform-boundary/v1``). Solo evidencia: no es juicio.

Para que la equivalencia metodológica (``governed_evidence``, v2) pueda normalizar la PROFUNDIDAD de resolución de objetos provistos por la
plataforma (p. ej. el ad hoc en vivo resuelve ``SAP.TIME.VIEW_DIMENSION_DAY`` hasta ``SAP.TIME.M_TIME_DIMENSION`` y el pool no), el perfil
publica, sin tocar ``canonical_evidence`` ni ninguna versión del juicio:

  · objects ......... objetos del cierre con su BASE de clasificación de plataforma. La base explícita hoy reconocida es
                      ``TIME_DIMENSION_SETTING``: la definición del repositorio trae ``timeDimensionSetting`` con ESE nombre técnico (objetos de
                      «Time Data» generados por Datasphere). Un objeto que el motor trata como de sistema SOLO por el espacio de nombres ``SAP.``
                      queda con base ``NAMESPACE_ONLY``: no acredita nada por sí mismo. La señal NO se generaliza a otros objetos SAP.
  · references ...... aristas del grafo de dependencias con al menos un extremo de plataforma: ``RESOLVED`` (ambos en el pool) o
                      ``PLATFORM_PROVIDED`` (el destino no está en el pool y el motor lo reconoce como objeto de plataforma).
  · direct_consumers  por objeto de plataforma, los miembros del cierre que lo referencian DIRECTAMENTE (incluido el modelo analítico raíz).

Contrato EXTENSIBLE: una base nueva se agrega en ``_markers`` con su propia señal explícita; nunca por prefijo de nombre.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

PLATFORM_BOUNDARY_VERSION = "platform-boundary/v1"
BASIS_TIME_DIMENSION_SETTING = "TIME_DIMENSION_SETTING"
BASIS_NAMESPACE_ONLY = "NAMESPACE_ONLY"
RESOLVED, PLATFORM_PROVIDED = "RESOLVED", "PLATFORM_PROVIDED"
BINDING_METHOD = "sha256:canonical-assessment-json"   # la MISMA firma canónica del artefacto (``profile.canonical_assessment_json``); no se crea otra
_TDS_TOKEN = b'"timeDimensionSetting"'


def _is_system_name(name: str) -> bool:
    from .identity import _is_system_name as engine_system_name
    return engine_system_name(name)


def _markers(files: Iterable[Path]) -> dict[str, dict]:
    """Objeto → marca explícita de plataforma leída de SU definición. ``timeDimensionSetting`` solo acredita los nombres que la MISMA
    definición declara en ``definitions`` (nunca un nombre ajeno al archivo)."""
    out: dict[str, dict] = {}
    for path in files:
        try:
            raw = Path(path).read_bytes()
        except OSError:
            continue
        if _TDS_TOKEN not in raw:
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        tds, defs = doc.get("timeDimensionSetting"), doc.get("definitions")
        if not isinstance(tds, dict) or not isinstance(defs, dict):
            continue
        for name, cfg in tds.items():
            if name not in defs or not isinstance(cfg, dict):
                continue
            ts = cfg.get("timesettings") if isinstance(cfg.get("timesettings"), dict) else {}
            out[name] = {"basis": BASIS_TIME_DIMENSION_SETTING, "status": ts.get("status"), "granularity": ts.get("granularity")}
    return out


def build_platform_boundary(universe: Any, root: str, files: Iterable[Path]) -> dict:
    """Bloque ``scope.platform_boundary`` del perfil. Determinista (ordenado)."""
    graph = universe.graph
    closure = tuple(universe.closure_of(root))
    closure_set = set(closure)
    markers = _markers(files)
    platform = {n for n in closure if n in markers or _is_system_name(n)}
    objects = [{"name": n, **(markers.get(n) or {"basis": BASIS_NAMESPACE_ONLY, "status": None, "granularity": None})} for n in sorted(platform)]
    refs: list[dict] = []
    for member in closure:
        for e in graph.edges_of(member):
            if e.provider in closure_set and (member in platform or e.provider in platform):
                refs.append({"from": member, "to": e.provider, "kind": e.kind.value, "resolution": RESOLVED})
    for u in graph.unresolved_of(closure):
        if u.consumer in closure_set and _is_system_name(u.target) and u.target not in closure_set:
            refs.append({"from": u.consumer, "to": u.target, "kind": u.kind.value, "resolution": PLATFORM_PROVIDED})
    uniq = {(r["from"], r["to"], r["kind"], r["resolution"]): r for r in refs}
    consumers: dict[str, list[str]] = {}
    for n in sorted(platform):
        consumers[n] = sorted({c for c in graph.consumers(n) if c in closure_set and c != n})
    return {"version": PLATFORM_BOUNDARY_VERSION, "objects": objects,
            "references": [uniq[k] for k in sorted(uniq)], "direct_consumers": consumers}


def profile_binding(doc: Mapping[str, Any]) -> dict:
    """Vínculo determinista del sidecar con EL perfil del que se derivó: sha256 de su JSON canónico (la firma del artefacto). Sin datos sensibles."""
    import hashlib
    from .profile import canonical_assessment_json
    return {"method": BINDING_METHOD, "digest": hashlib.sha256(canonical_assessment_json(dict(doc)).encode("utf-8")).hexdigest()}


def extensions(universe: Any, root: str | None, files: Iterable[Path]) -> dict:
    """Sidecar ``evidence_extensions`` (hermano del perfil, fuera de él). Sin raíz evaluable o ante un fallo: vacío (= no acreditado)."""
    if not root:
        return {}
    try:
        return {"platform_boundary": build_platform_boundary(universe, root, list(files))}
    except Exception:                                                     # noqa: BLE001 — evidencia complementaria: sin bloque = no acreditado
        return {}


def consumers_of(block: Mapping[str, Any] | None, name: str) -> list[str]:
    return list(((block or {}).get("direct_consumers") or {}).get(name) or [])
