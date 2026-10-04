"""Hecho de COMPOSICIÓN POR TIPO DE VISTA del cierre semántico (Gate 2 de "View Type Intelligence"). Solo HECHOS, sin juicio.

    am.view_type_composition   B  composición DESCRIPTIVA de las vistas de Datasphere del cierre semántico de la raíz evaluada:
                                  total de vistas ÚNICAS y cuántas son GRAPHICAL / SQL / UNKNOWN, más la lista de vistas con su tipo.

No se decide si "más SQL" o "más gráficas" es mejor ni peor: es evidencia objetiva (contexto de arquitectura), sin umbrales ni penalidades.

Qué cuenta como VISTA (precedencia aprobada):
  1. FAMILIA del artefacto, cuando el pool la informa: el archivo que define el artefacto cuelga de la carpeta-tipo `views`
     (normalizada con el contrato de carpetas del legado, vía ``legacy_bridge``). Es la fuente más fuerte: incluye las vistas UNKNOWN.
  2. Pools PLANOS (sin carpeta-tipo; exports históricos): solo la MISMA evidencia estructural que usa el exportador — marcador GRAPHICAL o
     marcador SQL — y excluyendo familias que no son vistas (modelos analíticos terminales, tablas locales, tablas remotas). Nunca por nombre
     de archivo. En un pool plano una vista sin ningún marcador no es reconocible, así que UNKNOWN solo es observable con familia informada.
Cada vista guarda por qué es vista (``membership_basis``: FAMILY_DIRECTORY | STRUCTURAL_MARKER) y cómo se obtuvo su tipo
(``view_type_basis``: STORED | DERIVED); son conceptos distintos.

Invariante (verificada al construir): graphical + sql + unknown == total. Sin vistas: porcentajes ``None``. No se cuentan AM, tablas ni flows.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import legacy_bridge as bridge
from .facts_support import make_fact
from .model import Area, EvidenceClass, EvidenceRef, Fact
from .readers import SupplementaryEvidence, ViewTypeEvidence
from .scope import AssessmentUniverse

AM_ = Area.ANALYTIC_MODEL

MEMBERSHIP_FAMILY = "FAMILY_DIRECTORY"
MEMBERSHIP_MARKER = "STRUCTURAL_MARKER"
VIEWS_FAMILY = "views"                       # carpeta-tipo normalizada del legado (`_catalog_dir_kind`)
ARTIFACT_FAMILY = "VIEW"                     # familia canónica del artefacto en el hecho (identidad estable para el Portfolio)


def _family_kind(ev: ViewTypeEvidence) -> str | None:
    return ev.family


def _is_excluded_non_view(entity: Any, name: str, universe: AssessmentUniverse) -> bool:
    """Familias que no son vistas, para el fallback estructural de pools planos."""
    if name in universe.terminal_ams:                                    # modelo analítico
        return True
    if str(getattr(entity, "repository_type", "") or "") == "DWC_LOCAL_TABLE":   # tabla local
        return True
    return bool(getattr(entity, "remote_entity", "") or "")              # tabla remota


def view_membership(ev: ViewTypeEvidence | None, entity: Any, name: str, universe: AssessmentUniverse) -> str | None:
    """Base por la que ``name`` es una vista, o None si no lo es. Función pura y determinista (la usa el hecho y la prueban los tests)."""
    if ev is None:
        return None
    family = _family_kind(ev)
    if family is not None:                                               # el pool informa la familia: manda
        return MEMBERSHIP_FAMILY if family == VIEWS_FAMILY else None
    if ev.view_type in (bridge.GRAPHICAL, bridge.SQL) and not _is_excluded_non_view(entity, name, universe):
        return MEMBERSHIP_MARKER
    return None


def _ratio(n: int, total: int) -> float | None:
    return round(n / total, 6) if total else None


def view_type_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    root: str,
    *,
    evidence: SupplementaryEvidence,
) -> tuple[Fact, ...]:
    closure = universe.closure_of(root)                                  # nombres únicos: la deduplicación es por construcción
    views: list[dict] = []
    for name in sorted(set(closure)):
        ev = evidence.view_type_evidence(name)
        basis = view_membership(ev, entities.get(name), name, universe)
        if basis is None:
            continue
        views.append({"technical_name": name, "artifact_family": ARTIFACT_FAMILY, "view_type": ev.view_type, "view_type_basis": ev.view_type_basis,
                      "membership_basis": basis, "source_file": ev.source_file})
    counts = {t: sum(1 for v in views if v["view_type"] == t) for t in bridge.VIEW_TYPES}
    total = len(views)
    assert counts[bridge.GRAPHICAL] + counts[bridge.SQL] + counts[bridge.UNKNOWN] == total   # invariante de la composición
    value = {
        "total_views": total,
        "graphical_views": counts[bridge.GRAPHICAL], "sql_views": counts[bridge.SQL], "unknown_views": counts[bridge.UNKNOWN],
        "graphical_view_pct": _ratio(counts[bridge.GRAPHICAL], total), "sql_view_pct": _ratio(counts[bridge.SQL], total),
        "unknown_view_pct": _ratio(counts[bridge.UNKNOWN], total),
        "views": views,
        "definition": ("unique Datasphere view artifacts in the semantic closure of the evaluated root; view_type precedence = GRAPHICAL marker, "
                       "else SQL marker, else UNKNOWN; descriptive evidence only (no judgment)"),
    }
    refs = tuple(EvidenceRef(v["technical_name"], "viewType", v["source_file"]) for v in views)
    return (make_fact("am.view_type_composition", AM_, root, value, EvidenceClass.B, refs=refs),)
