"""Universo de Modelos Analiticos terminales y resolucion de scope (M1) — sin evaluacion.

Responde "QUE ESTA EN SCOPE?". La unidad canonica del assessment es

    MODELO ANALITICO TERMINAL  +  su CLAUSURA SEMANTICA RECURSIVA de dependencias

nunca "todo el cliente": que dos artefactos pertenezcan al mismo cliente no los pone en el scope de
un AM. Un cliente con varios AM terminales requiere una SELECCION explicita.

`AssessmentUniverse` indexa la contencion (que AM terminales contienen a cada artefacto en su
clausura). Es solo el HECHO estructural: de aqui no se deriva ninguna senal de reuso ni calidad.

Plano Processing: el universo se construye UNICAMENTE desde las entidades del plano Modeling. Los
targets de Data Flow (`is_dataflow`) que el loader mezcla en el pool quedan FUERA: no son terminales, no
integran ninguna clausura, indice de contencion ni validacion de referencias; se exponen solo como
`processing_context` complementario. No se leen ni agregan artefactos RF/DF/TF/TC. Esa evidencia, mas
adelante, se adjunta como evidencia cross-plane sin cambiar el scope ni los denominadores.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .graph import DependencyGraph, ProcessingContext, UnresolvedReference
from .model import Applicability, ApplicabilityDecision, ApplicabilityReason, ProfileScope


class UnknownTerminalAmError(ValueError):
    """El AM pedido no es un Modelo Analitico terminal del universo."""


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
    ) -> None:
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
        cls, entities: Mapping[str, Any], *, is_terminal: Callable[[Any], bool] | None = None,
    ) -> "AssessmentUniverse":
        if is_terminal is None:
            from .legacy_bridge import is_cube       # criterio legacy de "modelo analitico / cubo"
            is_terminal = is_cube
        graph = DependencyGraph.from_entities(entities)               # excluye los targets de Data Flow
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

    def closure_of(self, am: str) -> tuple[str, ...]:
        """Clausura SEMANTICA recursiva (lineage + association) del AM, incluido el AM."""
        self._require_terminal(am)
        return self._closures[am]

    def lineage_closure_of(self, am: str) -> tuple[str, ...]:
        """Subconjunto alcanzable SOLO por LINEAGE (espina de composicion), incluido el AM."""
        self._require_terminal(am)
        return self._lineage[am]

    def association_only_closure_of(self, am: str) -> tuple[str, ...]:
        """Artefactos del scope alcanzables SOLO por asociacion (no por linaje). Hecho estructural, sin juicio."""
        lineage = set(self.lineage_closure_of(am))
        return tuple(a for a in self.closure_of(am) if a not in lineage)

    def feeds(self, source: str, am: str) -> bool:
        """¿`source` alimenta a `am` por linaje estructural? (las asociaciones no cuentan)."""
        self._require_terminal(am)
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

    # ── scope del assessment ───────────────────────────────────────────────
    def scope_for(self, am: str | None, *, pool_signature: str | None = None) -> ProfileScope:
        """`ProfileScope` (contrato M0) de UN AM terminal.

        * sin AM terminales y sin seleccion -> scope NOT_APPLICABLE (`NO_TERMINAL_AM`);
        * con AM terminales y sin seleccion  -> `TerminalAmSelectionRequired` (no se asume el cliente);
        * AM inexistente / no terminal       -> `UnknownTerminalAmError`.
        """
        if am is None:
            if self._terminal:
                raise TerminalAmSelectionRequired(
                    f"el universo tiene {len(self._terminal)} AM terminales {list(self._terminal)}: elegir uno")
            return ProfileScope(
                ApplicabilityDecision(Applicability.NOT_APPLICABLE, ApplicabilityReason.NO_TERMINAL_AM),
                None, 0, (), pool_signature)
        self._require_terminal(am)
        closure = self._closures[am]
        return ProfileScope(ApplicabilityDecision(Applicability.APPLICABLE), am, len(closure), closure,
                            pool_signature)
