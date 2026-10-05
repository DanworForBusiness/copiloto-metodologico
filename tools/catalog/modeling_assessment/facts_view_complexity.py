"""Hechos de COMPLEJIDAD ESTRUCTURAL de las vistas del cierre (S6, evidence release). Solo HECHOS descriptivos.

``harmonization.view_complexity`` (subject = vista del cierre con query compilado): el tipo de vista (GRAPHICAL | SQL | UNKNOWN y su base) como UNA dimension
descriptiva mas, junto con metricas estructurales extraidas del arbol compilado (ver ``complexity.py``) y los dependientes de la vista dentro del cierre.

Reglas de diseno (decision del owner, Gate 8A.2): NO hay umbral, NO hay puntaje compuesto y NO hay direccionalidad: ``GRAPHICAL`` no es malo ni ``SQL`` es
bueno. En la poblacion disponible (386 vistas de 5 pools) la relacion entre el tipo de vista y la complejidad es debil (correlacion biserial por rangos ~ -0,17):
una revision de mantenibilidad solo puede mostrar estos hechos y la distribucion del modelo; la convencion del proyecto es contexto humano. Esta evidencia es un
CALIFICADOR / revision de mantenibilidad, no una de las cuatro dimensiones metodologicas centrales.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import contract as C
from .facts_support import entity_refs, make_fact, sort_facts
from .graph import EdgeKind
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, Fact
from .scope import AssessmentUniverse

H_ = Area.HARMONIZATION
L = EdgeKind.LINEAGE
VIEW_TYPES = ("GRAPHICAL", "SQL", "UNKNOWN")


def view_complexity_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    if evidence is None:
        return ()
    graph = universe.graph
    closure = universe.closure_of(am)
    closure_set = set(closure)
    ident = identity or {n: classify(entities[n]) for n in closure}
    facts = []
    for n in closure:
        if n == am or (n in ident and ident[n].identity.value == "SYSTEM"):
            continue
        st = evidence.view_structure_evidence(n)
        if st is None:
            continue
        vt = evidence.view_type_evidence(n)
        m = dict(st.metrics)
        provider_names = [p for p in m.pop("provider_names") if p in entities]
        lay = C.layer_inference(entities[n])
        facts.append(make_fact(
            "harmonization.view_complexity", H_, n,
            {"artifact": n, "view_type": vt.view_type if vt is not None else "UNKNOWN", "view_type_basis": vt.view_type_basis if vt is not None else None, **lay,
             "providers": len(provider_names), "provider_names": provider_names, "providers_in_closure": sum(1 for p in provider_names if p in closure_set),
             "dependents_in_closure": len([c for c in graph.consumers(n, [L]) if c in closure_set]) if graph.has_node(n) else 0,
             "metrics": m},
            EvidenceClass.B, refs=entity_refs(entities[n], ["fields"])))
    return sort_facts(facts)
