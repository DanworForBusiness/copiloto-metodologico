"""Universo de Modelos Analiticos terminales y resolucion de scope (M1) — sin evaluacion.

Responde "QUE ESTA EN SCOPE?". La unidad canonica del assessment es

    MODELO ANALITICO TERMINAL  +  su CLAUSURA SEMANTICA RECURSIVA de dependencias

nunca "todo el cliente": que dos artefactos pertenezcan al mismo cliente no los pone en el scope de
un AM. Un cliente con varios AM terminales requiere una SELECCION explicita.

`AssessmentUniverse` indexa la contencion (que AM terminales contienen a cada artefacto en su
clausura). Es solo el HECHO estructural: de aqui no se deriva ninguna senal de reuso ni calidad.

Plano Processing (``closure-rules/v1``): el universo se construye desde las entidades del plano Modeling. Una entidad que
el loader marco como target de Data Flow (`is_dataflow`) pero que un artefacto de Modeling LEE es un miembro HOJA del cierre (su
procedencia la explica el flujo); la marcada que ningun artefacto de Modeling lee queda FUERA (no es terminal, no integra ninguna
clausura, indice de contencion ni validacion de referencias) y se expone solo como `processing_context` complementario. No se
leen ni agregan artefactos RF/DF/TF/TC al cierre: esa evidencia se adjunta como evidencia cross-plane sin cambiar los denominadores.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .graph import PLANE_POLICY_MODELING_MEMBERSHIP, DependencyGraph, ProcessingContext, UnresolvedReference
from .model import (
    Applicability, ApplicabilityDecision, ApplicabilityReason, ProfileScope, ROOT_TYPE_ANALYTIC_MODEL, ROOT_TYPES,
)


class UnknownTerminalAmError(ValueError):
    """El AM pedido no es un Modelo Analitico terminal del universo."""


class UnknownRootObjectError(UnknownTerminalAmError):
    """El objeto raiz pedido (modo generico) no existe como artefacto del grafo de Modeling de este pool."""


class TerminalAmSelectionRequired(ValueError):
    """Hay Modelos Analiticos terminales y no se eligio ninguno: no se infiere un assessment de cliente."""


@dataclass(frozen=True)
class ClosureEvidence:
    """Evidencia de grafo relevante para una clausura (nunca se silencia ni se fabrica)."""
    unresolved: tuple[UnresolvedReference, ...]
    cycles: tuple[tuple[str, ...], ...]


class AssessmentUniverse:
    """Un universo = el pool de entidades de UN cliente/dominio tal como lo carga el legacy."""

    def __init__(
        self,
        graph: DependencyGraph,
        terminal_ams: tuple[str, ...],
        closures: Mapping[str, tuple[str, ...]],
        lineage_closures: Mapping[str, tuple[str, ...]],
        extra_roots: Mapping[str, tuple[str, tuple[str, ...], tuple[str, ...]]] | None = None,
    ) -> None:
        # extra_roots: raices que NO son AM terminal (vista / tabla): nombre -> (root_type, clausura semantica, clausura de linaje). No entran en
        # `terminal_ams` ni en el indice de contencion cross-AM (el reuso entre AM es un concepto de AM).
        self._extra = MappingProxyType({r: (t, tuple(c), tuple(l)) for r, (t, c, l) in (extra_roots or {}).items()})
        self._graph = graph
        self._terminal = tuple(sorted(terminal_ams))
        self._closures = MappingProxyType({am: tuple(closures[am]) for am in self._terminal})
        self._lineage = MappingProxyType({am: tuple(lineage_closures[am]) for am in self._terminal})
        containing: dict[str, list[str]] = {}
        for am in self._terminal:
            for artifact in self._closures[am]:
                containing.setdefault(artifact, []).append(am)
        self._containing = MappingProxyType({a: tuple(sorted(v)) for a, v in sorted(containing.items())})

    @classmethod
    def from_entities(
        cls, entities: Mapping[str, Any], *, is_terminal: Callable[[Any], bool] | None = None, plane_policy: str = PLANE_POLICY_MODELING_MEMBERSHIP,
    ) -> "AssessmentUniverse":
        if is_terminal is None:
            from .legacy_bridge import is_cube       # criterio legacy de "modelo analitico / cubo"
            is_terminal = is_cube
        graph = DependencyGraph.from_entities(entities, plane_policy)  # un target de flujo que Modeling lee es miembro; el contexto de Processing queda fuera
        terminal = tuple(sorted(n for n, e in entities.items() if graph.has_node(n) and is_terminal(e)))
        closures = {am: graph.semantic_closure(am) for am in terminal}
        lineage = {am: graph.lineage_closure(am) for am in terminal}
        return cls(graph, terminal, closures, lineage)

    # ── inventario y resolucion ────────────────────────────────────────────
    @property
    def graph(self) -> DependencyGraph:
        return self._graph

    @property
    def processing_context(self) -> ProcessingContext:
        """Contexto COMPLEMENTARIO de Processing (targets de Data Flow); fuera de todo recorrido de Modeling."""
        return self._graph.processing_context

    @property
    def terminal_ams(self) -> tuple[str, ...]:
        return self._terminal

    def _require_terminal(self, am: str) -> None:
        if am not in self._closures:
            raise UnknownTerminalAmError(f"{am!r} no es un Modelo Analitico terminal de este universo")

    def _require_root(self, root: str) -> None:
        if root not in self._closures and root not in self._extra:
            raise UnknownTerminalAmError(f"{root!r} no es una raiz de assessment de este universo")

    def root_type_of(self, root: str) -> str:
        """``analytic-model`` para un AM terminal; el tipo registrado para una raiz generica (vista / tabla)."""
        self._require_root(root)
        return ROOT_TYPE_ANALYTIC_MODEL if root in self._closures else self._extra[root][0]

    def closure_of(self, am: str) -> tuple[str, ...]:
        """Clausura SEMANTICA recursiva (lineage + association) de la raiz, incluida la raiz."""
        self._require_root(am)
        return self._closures[am] if am in self._closures else self._extra[am][1]

    def lineage_closure_of(self, am: str) -> tuple[str, ...]:
        """Subconjunto alcanzable SOLO por LINEAGE (espina de composicion), incluida la raiz."""
        self._require_root(am)
        return self._lineage[am] if am in self._lineage else self._extra[am][2]

    def association_only_closure_of(self, am: str) -> tuple[str, ...]:
        """Artefactos del scope alcanzables SOLO por asociacion (no por linaje). Hecho estructural, sin juicio."""
        lineage = set(self.lineage_closure_of(am))
        return tuple(a for a in self.closure_of(am) if a not in lineage)

    def feeds(self, source: str, am: str) -> bool:
        """¿`source` alimenta a `am` por linaje estructural? (las asociaciones no cuentan)."""
        self._require_root(am)
        return self._graph.feeds(source, am)

    def evidence_for(self, am: str) -> ClosureEvidence:
        """Referencias sin resolver y ciclos que tocan la clausura del AM."""
        closure = set(self.closure_of(am))
        cycles = tuple(c for c in self._graph.cycles() if closure.intersection(c))
        return ClosureEvidence(self._graph.unresolved_of(closure), cycles)

    # ── indice de contencion cross-AM (hecho estructural, sin juicio) ──────
    @property
    def containment_index(self) -> Mapping[str, tuple[str, ...]]:
        """artefacto -> AM terminales (ordenados) cuya clausura semantica lo contiene."""
        return self._containing

    def terminal_ams_containing(self, artifact: str) -> tuple[str, ...]:
        return self._containing.get(artifact, ())

    def other_terminal_ams_containing(self, artifact: str, *, excluding: str) -> tuple[str, ...]:
        """AM terminales DISTINTOS de `excluding` cuya clausura contiene `artifact`.
        Base estructural (mas adelante) de la distincion LOCAL_CHAIN_REUSE vs CROSS_AM_REUSE;
        aqui es solo el hecho: no se calcula senal, bonus ni juicio."""
        self._require_terminal(excluding)
        return tuple(am for am in self._containing.get(artifact, ()) if am != excluding)

    # ── raiz generica (vista / tabla / AM) ─────────────────────────────────
    def resolve_root(self, entities: Mapping[str, Any], root: str, hint: str | None = None, *,
                     pool_signature: str | None = None) -> "tuple[AssessmentUniverse, ProfileScope]":
        """Universo + scope de CUALQUIER objeto raiz soportado: su clausura semantica UPSTREAM (la raiz siempre incluida; sin analisis downstream).
        Un AM terminal sigue el camino de siempre. Una raiz que no es AM terminal se registra como raiz generica; su tipo es el que informa el
        extractor (``hint``, p. ej. ``view``) o, sin hint, el inferido de la evidencia. Si el objeto no existe en el grafo: UnknownRootObjectError."""
        if root in self._closures:
            return self, self.scope_for(root, pool_signature=pool_signature)
        if not self._graph.has_node(root) or root not in entities:
            raise UnknownRootObjectError(f"{root!r} no existe como artefacto de Modeling en este pool")
        root_type = hint if hint in ROOT_TYPES else self._infer_root_type(entities[root], root)
        if root_type == ROOT_TYPE_ANALYTIC_MODEL:                       # el extractor lo declara AM aunque la evidencia no lo marque como cubo
            closure, lineage = self._graph.semantic_closure(root), self._graph.lineage_closure(root)
            extra = {**self._extra, root: (root_type, closure, lineage)}
            return self._with_extra(extra), ProfileScope(ApplicabilityDecision(Applicability.APPLICABLE), root, len(closure), closure,
                                                         pool_signature, root_type)
        closure, lineage = self._graph.semantic_closure(root), self._graph.lineage_closure(root)
        extra = {**self._extra, root: (root_type, closure, lineage)}
        return self._with_extra(extra), ProfileScope(ApplicabilityDecision(Applicability.APPLICABLE), None, len(closure), closure,
                                                     pool_signature, root_type, root)

    def _with_extra(self, extra: Mapping[str, tuple[str, tuple[str, ...], tuple[str, ...]]]) -> "AssessmentUniverse":
        return AssessmentUniverse(self._graph, self._terminal, self._closures, self._lineage, extra)

    def _infer_root_type(self, entity: Any, root: str) -> str:
        if getattr(entity, "remote_entity", ""):
            return "remote-table"
        return "view" if self._graph.providers(root) else "local-table"

    # ── scope del assessment ───────────────────────────────────────────────
    def scope_for(self, am: str | None, *, pool_signature: str | None = None) -> ProfileScope:
        """`ProfileScope` (contrato M0) de UN AM terminal.

        * sin AM terminales y sin seleccion -> scope NOT_APPLICABLE (`NO_TERMINAL_ANALYTIC_MODEL`);
        * con AM terminales y sin seleccion  -> `TerminalAmSelectionRequired` (no se asume el cliente);
        * AM inexistente / no terminal       -> `UnknownTerminalAmError`.
        """
        if am is None:
            if self._terminal:
                raise TerminalAmSelectionRequired(
                    f"el universo tiene {len(self._terminal)} AM terminales {list(self._terminal)}: elegir uno")
            return ProfileScope(
                ApplicabilityDecision(Applicability.NOT_APPLICABLE, ApplicabilityReason.NO_TERMINAL_ANALYTIC_MODEL),
                None, 0, (), pool_signature)
        self._require_terminal(am)
        closure = self._closures[am]
        return ProfileScope(ApplicabilityDecision(Applicability.APPLICABLE), am, len(closure), closure,
                            pool_signature)
