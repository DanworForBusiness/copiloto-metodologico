"""Grafo de dependencias TIPADO del Modeling (M1) — sustrato estructural, SIN evaluacion.

Responde "COMO ESTA CONECTADO?". No emite observaciones de calidad ni severidad.

Dos tipos de arista (nunca una arista indiferenciada); el tipo lo define el ORIGEN SEMANTICO de la
referencia, NO el contenedor del loader donde termino:

  LINEAGE      A consume / esta construido a partir de B (FROM / JOIN / composicion de fuentes del
               query). Es la ESPINA de composicion de datos.
  ASSOCIATION  A se asocia semanticamente a B (asociacion declarada del modelo). Enriquece el alcance
               semantico pero NO implica que filas de B alimenten fisicamente a A.

Dos recorridos (conceptos distintos, nunca intercambiables):

  semantic_closure(root)  LINEAGE + ASSOCIATION, recursivo. Es el alcance del assessment: por eso
                          las dimensiones compartidas pertenecen al contexto de evaluacion.
  lineage_closure(root)   SOLO LINEAGE. Responde "¿X alimenta estructuralmente al producto?".
                          Una arista ASSOCIATION NUNCA cuenta como prueba de que filas alimentan.

"Esta dentro de la clausura del AM" y "alimenta al AM por linaje" NO son equivalentes.

RECUPERACION DE LINAJE (correccion de M1). El loader legacy (que NO se modifica) DEGRADA a
`Entity.assoc_upstreams` los inputs FROM/JOIN de las entidades analiticas (hecho/dimension/cubo) que no
son una base transformacional (dimensiones, textos, fuentes crudas). Eso es un artefacto de contenedor, no
semantica: si se respetara, las dimensiones `VD_*` parecerian "solo asociadas" a su fuente remota y el
alcance por linaje quedaria incompleto. Regla canonica:

  * referencia presente en `Entity._raw_refs` (que son EXCLUSIVAMENTE referencias FROM/JOIN del query:
    `_extract_query_sources` + FROM/JOIN del SQL preview)  ->  arista LINEAGE
        origen UPSTREAM_FIELD         si ya estaba en `Entity.upstreams`
        origen QUERY_SOURCE_RECOVERED si el loader la habia degradado a `assoc_upstreams`
  * asociacion declarada (`Entity.associations[].target`)    ->  arista ASSOCIATION (DECLARED_ASSOCIATION)
  * el mismo objetivo puede ser ambas cosas (FROM/JOIN y asociacion declarada): se emiten las DOS aristas
  * target en `assoc_upstreams` sin declaracion ni evidencia FROM/JOIN verificable (p.ej. no hay
    `_raw_refs`) -> ASSOCIATION con origen ASSOCIATION_FIELD_UNDETERMINED (no se infiere linaje sin prueba)
  * identificador de `_raw_refs` que no resuelve a un artefacto (alias interno de vista grafica como
    `Union 1` / `$projection`, tabla `_Delta` sombra, u objeto realmente ausente) -> evidencia
    `UNVERIFIED_QUERY_REFERENCE`: nunca se fabrica un nodo, no amplia el scope y NO es `MISSING_TARGET`.
  * `MISSING_TARGET` es ESTRICTO: la metadata declara una identidad concreta de artefacto modelado, la relacion
    es confiable y el target deberia resolver en el universo pero no existe. Nunca se infiere de residuo del parser.

FRONTERA DE PLANOS (``closure-rules/v1``). Un artefacto es MIEMBRO del grafo de Modeling por lo que el Modeling hace con el, no por quien lo produce:
una tabla local que un artefacto de Modeling LEE pertenece al cierre semantico aunque un Data Flow la produzca (y aunque el loader legacy la haya
marcado ``Entity.is_dataflow`` por vivir en un archivo ``DF_*`` sin query ni remote.entity). El flujo productor NO es un artefacto semantico: es contexto
de procedencia (``ProcessingContext.flow_produced_members`` + ``source.leaf_provenance`` / ``source.physical_boundary``). El linaje que el loader inyecta a
esa tabla desde el DF se ignora (la tabla es una HOJA del cierre: sus fuentes son procedencia, no linaje semantico). Solo es contexto de Processing
(fuera del grafo) la entidad marcada que NINGUN artefacto de Modeling lee; una referencia a una entidad asi se registra como ``BoundaryReference``.
La politica anterior (todo lo marcado por el loader queda fuera) se conserva SOLO como modo explicito ``LOADER_FILE_FLAG`` para reproducir el cierre
previo (atribucion de diferencias en la migracion de evidencia y pruebas); ya no es el comportamiento por defecto.

Recorridos deterministas, iterativos (sin recursion), seguros ante ciclos y duplicados.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping


class EdgeKind(str, Enum):
    LINEAGE = "LINEAGE"
    ASSOCIATION = "ASSOCIATION"


class EdgeOrigin(str, Enum):
    # LINEAGE
    UPSTREAM_FIELD = "UPSTREAM_FIELD"                                  # estaba en `Entity.upstreams`
    QUERY_SOURCE_RECOVERED = "QUERY_SOURCE_RECOVERED"                  # FROM/JOIN degradado por el loader a `assoc_upstreams`
    # ASSOCIATION
    DECLARED_ASSOCIATION = "DECLARED_ASSOCIATION"                      # `Entity.associations[].target`
    ASSOCIATION_FIELD_UNDETERMINED = "ASSOCIATION_FIELD_UNDETERMINED"  # solo en `assoc_upstreams`; sin prueba de FROM/JOIN


_ORIGIN_KIND = {
    EdgeOrigin.UPSTREAM_FIELD: EdgeKind.LINEAGE,
    EdgeOrigin.QUERY_SOURCE_RECOVERED: EdgeKind.LINEAGE,
    EdgeOrigin.DECLARED_ASSOCIATION: EdgeKind.ASSOCIATION,
    EdgeOrigin.ASSOCIATION_FIELD_UNDETERMINED: EdgeKind.ASSOCIATION,
}


class UnresolvedReason(str, Enum):
    MISSING_TARGET = "MISSING_TARGET"                          # referencia DECLARADA y confiable a un artefacto que no existe en el pool
    SELF_REFERENCE = "SELF_REFERENCE"                          # referencia DECLARADA de un artefacto a si mismo
    UNVERIFIED_QUERY_REFERENCE = "UNVERIFIED_QUERY_REFERENCE"  # identificador FROM/JOIN del query que no resuelve (alias interno,
                                                               # `_Delta` sombra u objeto ausente): NO se afirma que falte


class UnknownNodeError(KeyError):
    """Se pidio un recorrido desde/hacia un nodo que no existe en el grafo."""


@dataclass(frozen=True)
class Edge:
    """`consumer` depende de `provider`."""
    consumer: str
    provider: str
    kind: EdgeKind
    origin: EdgeOrigin

    def __post_init__(self) -> None:
        if _ORIGIN_KIND[self.origin] is not self.kind:
            raise ValueError(f"origen {self.origin.value} incompatible con el tipo {self.kind.value}")


@dataclass(frozen=True)
class UnresolvedReference:
    """Evidencia explicita de una relacion que no pudo resolverse (nunca se fabrica un nodo)."""
    consumer: str
    target: str
    kind: EdgeKind
    reason: UnresolvedReason
    evidence_source: str            # p.ej. "associations", "_raw_refs", "upstreams", "assoc_upstreams"


@dataclass(frozen=True)
class BoundaryReference:
    """Referencia de un artefacto de Modeling a un target de Data Flow (contexto de Processing)."""
    consumer: str
    target: str
    kind: EdgeKind
    evidence_source: str


@dataclass(frozen=True)
class ProcessingContext:
    """Contexto COMPLEMENTARIO de Processing visto desde el pool de Modeling. No participa de ningun recorrido.

    ``dataflow_targets``: entidades marcadas por el loader que ningun artefacto de Modeling lee (fuera del grafo).
    ``flow_produced_members``: entidades marcadas por el loader que Modeling SI lee: miembros del grafo (hojas) cuya procedencia explica un flujo."""
    dataflow_targets: tuple[str, ...]
    boundary_references: tuple[BoundaryReference, ...]
    flow_produced_members: tuple[str, ...] = ()


PLANE_POLICY_MODELING_MEMBERSHIP = "MODELING_MEMBERSHIP"     # por defecto (closure-rules/v1)
PLANE_POLICY_LOADER_FILE_FLAG = "LOADER_FILE_FLAG"           # reproduce el cierre previo al versionado explicito del cierre
PLANE_POLICIES = (PLANE_POLICY_MODELING_MEMBERSHIP, PLANE_POLICY_LOADER_FILE_FLAG)


def is_processing_context_entity(entity: Any) -> bool:
    """¿El loader marco la entidad como definida por un archivo de Data Flow (``is_dataflow``)? Es una MARCA de procedencia del archivo, no la pertenencia
    al plano: la membresia la decide ``processing_only_names`` (ver el docstring del modulo)."""
    return bool(getattr(entity, "is_dataflow", False))


def consumed_names(entities: Mapping[str, Any]) -> frozenset[str]:
    """Nombres que algun artefacto de Modeling (NO marcado por el loader) referencia: ``upstreams``, ``assoc_upstreams``, asociaciones declaradas y
    referencias FROM/JOIN del query (``_raw_refs``). Solo cuenta si el referenciado existe en el pool y no es el propio consumidor."""
    out: set[str] = set()
    for name, e in entities.items():
        if is_processing_context_entity(e):
            continue                                              # el linaje inyectado a un target de flujo no es un consumo de Modeling
        refs = set(_as_names(getattr(e, "upstreams", ()))) | set(_as_names(getattr(e, "assoc_upstreams", ())))
        refs |= {str(a.get("target")) for a in (getattr(e, "associations", None) or ()) if isinstance(a, Mapping) and a.get("target")}
        raw = getattr(e, "_raw_refs", None)
        if raw is not None:
            refs |= {str(x) for x in raw if x}
        out |= {r for r in refs if r in entities and r != name}
    return frozenset(out)


def processing_only_names(entities: Mapping[str, Any], plane_policy: str = PLANE_POLICY_MODELING_MEMBERSHIP) -> frozenset[str]:
    """Entidades que quedan FUERA del plano Modeling. Politica por defecto: marcadas por el loader Y no leidas por Modeling."""
    if plane_policy not in PLANE_POLICIES:
        raise ValueError(f"politica de plano desconocida: {plane_policy!r}")
    flagged = {n for n, e in entities.items() if is_processing_context_entity(e)}
    if plane_policy == PLANE_POLICY_LOADER_FILE_FLAG:
        return frozenset(flagged)
    return frozenset(flagged - consumed_names(entities))


def modeling_population(entities: Mapping[str, Any], plane_policy: str = PLANE_POLICY_MODELING_MEMBERSHIP) -> dict[str, Any]:
    """Las entidades del plano Modeling: el pool SIN las entidades de contexto de Processing (``processing_only_names``)."""
    out_names = processing_only_names(entities, plane_policy)
    return {n: e for n, e in entities.items() if n not in out_names}


def _sorted_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _as_names(raw: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in (raw or ()) if x)


class DependencyGraph:
    """Grafo inmutable de dependencias entre artefactos del plano Modeling."""

    def __init__(
        self,
        nodes: Iterable[str],
        edges: Iterable[Edge],
        unresolved: Iterable[UnresolvedReference] = (),
        processing_context: ProcessingContext | None = None,
        nodes_with_raw_refs: int = 0,
    ) -> None:
        self._nodes = _sorted_unique(nodes)
        node_set = set(self._nodes)
        by_key: dict[tuple[str, str, EdgeKind], Edge] = {}
        for e in edges:
            if e.consumer not in node_set or e.provider not in node_set:
                raise ValueError(f"arista con nodo inexistente: {e}")
            by_key.setdefault((e.consumer, e.provider, e.kind), e)
        self._edges = tuple(by_key[k] for k in sorted(by_key, key=lambda k: (k[0], k[1], k[2].value)))
        self._unresolved = tuple(sorted(set(unresolved), key=lambda u: (
            u.consumer, u.target, u.kind.value, u.reason.value, u.evidence_source)))
        self._processing = processing_context or ProcessingContext((), ())
        overlap = node_set.intersection(self._processing.dataflow_targets)
        if overlap:
            raise ValueError(f"targets de Data Flow dentro del grafo de Modeling: {sorted(overlap)}")
        self.nodes_with_raw_refs = int(nodes_with_raw_refs)
        prov: dict[str, dict[EdgeKind, list[str]]] = {n: {k: [] for k in EdgeKind} for n in self._nodes}
        cons: dict[str, dict[EdgeKind, list[str]]] = {n: {k: [] for k in EdgeKind} for n in self._nodes}
        for e in self._edges:
            prov[e.consumer][e.kind].append(e.provider)
            cons[e.provider][e.kind].append(e.consumer)
        self._providers = {n: {k: tuple(sorted(v)) for k, v in d.items()} for n, d in prov.items()}
        self._consumers = {n: {k: tuple(sorted(v)) for k, v in d.items()} for n, d in cons.items()}

    # ── construccion desde entidades del loader legacy ─────────────────────
    @classmethod
    def from_entities(cls, entities: Mapping[str, Any], plane_policy: str = PLANE_POLICY_MODELING_MEMBERSHIP) -> "DependencyGraph":
        """Construye desde el mapping `nombre -> entidad` que devuelve `load_folder` (duck-typed: se leen
        `upstreams`, `assoc_upstreams`, `associations`, `_raw_refs`, `is_dataflow`). ``plane_policy``: ver el docstring del modulo."""
        dataflow = set(processing_only_names(entities, plane_policy))                       # fuera del grafo (contexto de Processing)
        flow_produced = {n for n, e in entities.items() if is_processing_context_entity(e)} - dataflow   # miembros HOJA producidos por un flujo
        nodes = set(entities) - dataflow
        edges: list[Edge] = []
        unresolved: set[UnresolvedReference] = set()
        boundary: set[BoundaryReference] = set()
        with_raw = 0

        for name in sorted(nodes):
            e = entities[name]
            if name in flow_produced:
                continue                                       # hoja: el linaje que el loader le inyecto desde el DF es procedencia, no linaje semantico
            declared = {str(a.get("target")) for a in (getattr(e, "associations", None) or ())
                        if isinstance(a, Mapping) and a.get("target")}
            raw_attr = getattr(e, "_raw_refs", None)
            raw = None if raw_attr is None else {str(x) for x in raw_attr if x}
            if raw is not None:
                with_raw += 1
            upstreams = set(_as_names(getattr(e, "upstreams", ())))
            assoc_field = set(_as_names(getattr(e, "assoc_upstreams", ())))

            def classify_target(t: str, kind: EdgeKind, source: str, origin: EdgeOrigin) -> None:
                """Referencia DECLARADA (upstreams / assoc_upstreams / associations) a `t`."""
                if t == name:
                    unresolved.add(UnresolvedReference(name, t, kind, UnresolvedReason.SELF_REFERENCE, source))
                elif t in dataflow:
                    boundary.add(BoundaryReference(name, t, kind, source))
                elif t in nodes:
                    edges.append(Edge(name, t, kind, origin))
                else:
                    unresolved.add(UnresolvedReference(name, t, kind, UnresolvedReason.MISSING_TARGET, source))

            # LINEAGE declarado por el loader (`upstreams`).
            for t in sorted(upstreams):
                classify_target(t, EdgeKind.LINEAGE, "upstreams", EdgeOrigin.UPSTREAM_FIELD)

            # LINEAGE recuperado: FROM/JOIN del query que el loader degrado a `assoc_upstreams`.
            if raw is not None:
                for t in sorted((raw & nodes) - {name} - upstreams):
                    edges.append(Edge(name, t, EdgeKind.LINEAGE, EdgeOrigin.QUERY_SOURCE_RECOVERED))

            # `assoc_upstreams`: lo no explicable por declaracion ni por FROM/JOIN queda indeterminado (sin inferir linaje).
            for t in sorted(assoc_field):
                from_ref = raw is not None and t in raw
                if t in nodes and t != name and (t in declared or from_ref):
                    continue                                   # ya representado por una arista con origen demostrable
                classify_target(t, EdgeKind.ASSOCIATION, "assoc_upstreams", EdgeOrigin.ASSOCIATION_FIELD_UNDETERMINED)

            # Asociaciones DECLARADAS del modelo (hechos del modelo; el loader las puede haber absorbido en `upstreams`).
            for t in sorted(declared):
                classify_target(t, EdgeKind.ASSOCIATION, "associations", EdgeOrigin.DECLARED_ASSOCIATION)

            # Identificadores FROM/JOIN que no resuelven a un artefacto: evidencia NO verificada (nunca MISSING_TARGET).
            if raw is not None:
                for t in sorted(raw):
                    if t in dataflow:
                        boundary.add(BoundaryReference(name, t, EdgeKind.LINEAGE, "_raw_refs"))
                    elif t == name or t not in nodes:
                        unresolved.add(UnresolvedReference(
                            name, t, EdgeKind.LINEAGE, UnresolvedReason.UNVERIFIED_QUERY_REFERENCE, "_raw_refs"))

        ctx = ProcessingContext(
            tuple(sorted(dataflow)),
            tuple(sorted(boundary, key=lambda b: (b.consumer, b.target, b.kind.value, b.evidence_source))),
            tuple(sorted(flow_produced)))
        return cls(nodes, edges, unresolved, ctx, with_raw)

    # ── consulta ───────────────────────────────────────────────────────────
    @property
    def nodes(self) -> tuple[str, ...]:
        return self._nodes

    @property
    def edges(self) -> tuple[Edge, ...]:
        return self._edges

    @property
    def unresolved(self) -> tuple[UnresolvedReference, ...]:
        return self._unresolved

    @property
    def processing_context(self) -> ProcessingContext:
        return self._processing

    @property
    def recovered_lineage_edges(self) -> tuple[Edge, ...]:
        """Aristas LINEAGE que el loader habia degradado a asociacion y se recuperaron desde el FROM/JOIN."""
        return tuple(e for e in self._edges if e.origin is EdgeOrigin.QUERY_SOURCE_RECOVERED)

    def has_node(self, node: str) -> bool:
        return node in self._providers

    def _require(self, node: str) -> None:
        if node not in self._providers:
            raise UnknownNodeError(node)

    def _kinds(self, kinds: Iterable[EdgeKind] | None) -> frozenset[EdgeKind]:
        return frozenset(EdgeKind) if kinds is None else frozenset(kinds)

    def providers(self, node: str, kinds: Iterable[EdgeKind] | None = None) -> tuple[str, ...]:
        self._require(node)
        ks = self._kinds(kinds)
        return _sorted_unique(p for k in ks for p in self._providers[node][k])

    def consumers(self, node: str, kinds: Iterable[EdgeKind] | None = None) -> tuple[str, ...]:
        self._require(node)
        ks = self._kinds(kinds)
        return _sorted_unique(c for k in ks for c in self._consumers[node][k])

    def edges_of(self, consumer: str) -> tuple[Edge, ...]:
        self._require(consumer)
        return tuple(e for e in self._edges if e.consumer == consumer)

    def unresolved_of(self, consumers: Iterable[str]) -> tuple[UnresolvedReference, ...]:
        wanted = set(consumers)
        return tuple(u for u in self._unresolved if u.consumer in wanted)

    # ── recorridos ─────────────────────────────────────────────────────────
    def _closure(self, root: str, kinds: frozenset[EdgeKind]) -> tuple[str, ...]:
        self._require(root)
        seen = {root}
        queue = deque([root])
        while queue:
            cur = queue.popleft()
            for k in sorted(kinds, key=lambda k: k.value):
                for nxt in self._providers[cur][k]:
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
        return tuple(sorted(seen))

    def semantic_closure(self, root: str) -> tuple[str, ...]:
        """LINEAGE + ASSOCIATION recursivo; incluye `root`. Es el alcance del assessment."""
        return self._closure(root, frozenset(EdgeKind))

    def lineage_closure(self, root: str) -> tuple[str, ...]:
        """SOLO LINEAGE; incluye `root`. Una arista ASSOCIATION nunca cuenta como alimentacion."""
        return self._closure(root, frozenset({EdgeKind.LINEAGE}))

    def feeds(self, source: str, terminal: str) -> bool:
        """¿`source` alimenta estructuralmente a `terminal` por LINEAGE? (excluye el caso source==terminal)."""
        self._require(source)
        self._require(terminal)
        return source != terminal and source in self._closure(terminal, frozenset({EdgeKind.LINEAGE}))

    def distances(self, root: str, kinds: Iterable[EdgeKind] | None = None) -> Mapping[str, int]:
        """Distancia minima (numero de aristas) desde `root` hacia cada artefacto alcanzable, siguiendo
        dependencias (consumer -> provider) de los `kinds` indicados. `root` = 0. Hecho estructural, sin juicio."""
        self._require(root)
        ks = self._kinds(kinds)
        dist = {root: 0}
        queue = deque([root])
        while queue:
            cur = queue.popleft()
            for nxt in _sorted_unique(p for k in ks for p in self._providers[cur][k]):
                if nxt not in dist:
                    dist[nxt] = dist[cur] + 1
                    queue.append(nxt)
        return MappingProxyType(dist)

    def transitive_consumers(self, node: str, kinds: Iterable[EdgeKind] | None = None) -> tuple[str, ...]:
        """Todos los artefactos que dependen (directa o transitivamente) de `node` por los `kinds` indicados;
        excluye a `node`. Hecho estructural, sin juicio."""
        self._require(node)
        ks = self._kinds(kinds)
        seen = {node}
        queue = deque([node])
        while queue:
            cur = queue.popleft()
            for nxt in _sorted_unique(c for k in ks for c in self._consumers[cur][k]):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return tuple(sorted(seen - {node}))

    def cycles(self, kinds: Iterable[EdgeKind] | None = None) -> tuple[tuple[str, ...], ...]:
        """Componentes fuertemente conexas de >1 nodo (Tarjan iterativo), ordenadas y deterministas."""
        ks = self._kinds(kinds)
        adj = {n: _sorted_unique(p for k in ks for p in self._providers[n][k]) for n in self._nodes}
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        out: list[tuple[str, ...]] = []
        counter = 0
        for start in self._nodes:
            if start in index:
                continue
            index[start] = low[start] = counter
            counter += 1
            stack.append(start)
            on_stack.add(start)
            work = [(start, iter(adj[start]))]
            while work:
                v, it = work[-1]
                advanced = False
                for w in it:
                    if w not in index:
                        index[w] = low[w] = counter
                        counter += 1
                        stack.append(w)
                        on_stack.add(w)
                        work.append((w, iter(adj[w])))
                        advanced = True
                        break
                    if w in on_stack:
                        low[v] = min(low[v], index[w])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
                if low[v] == index[v]:
                    comp: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        out.append(tuple(sorted(comp)))
        return tuple(sorted(out))

    def edge_counts(self) -> Mapping[str, int]:
        c = {k.value: 0 for k in EdgeKind}
        for e in self._edges:
            c[e.kind.value] += 1
        return MappingProxyType(c)
