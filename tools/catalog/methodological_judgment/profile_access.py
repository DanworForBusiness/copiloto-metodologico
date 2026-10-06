"""Acceso de SOLO LECTURA, tipado, al perfil PUBLICO persistido de un Assessment (``profile_json``).

Existe para que los CONSUMIDORES HERMANOS de la evidencia canonica (el motor de Juicio metodologico y, mas adelante, un motor de Clasificacion de Dominio) lean el
mismo perfil sin duplicar la logica de extraccion. NO calcula juicio, NO decide calidad, NO escribe, NO toca el filesystem ni la red y NUNCA muta el perfil.

Todo lo derivado aqui sale del perfil persistido (``canonical_evidence`` v2 + ``areas`` + ``review_required``/``observations`` + ``fact_index`` + ``scope``): no hay
ninguna lectura del pool de artefactos ni del veredicto legacy.
"""
from __future__ import annotations

from collections import deque
from functools import cached_property
from typing import Any, Iterable, Mapping

SYSTEM_NAMESPACE = "SAP"

# clases de contacto con la fuente (politica de contrato de fuente, Gate 8C.1)
GOVERNED_SAP_CONTRACT = "GOVERNED_SAP_CONTRACT"
PHYSICAL_RAW_BOUNDARY = "PHYSICAL_RAW_BOUNDARY"
EXTERNAL_CONTRACT_UNDETERMINED = "EXTERNAL_CONTRACT_UNDETERMINED"
SOURCE_CLASSES = (GOVERNED_SAP_CONTRACT, PHYSICAL_RAW_BOUNDARY, EXTERNAL_CONTRACT_UNDETERMINED)
EXTERNAL_POLICIES = ("UNDETERMINED", "PHYSICAL_RAW", "GOVERNED")                     # solo para PRUEBAS de sensibilidad; el motor usa UNDETERMINED

# clases de proveedor de un nodo de composicion
K_HARM, K_VIEW, K_LOCAL_LEAF, K_SYSTEM = "HARMONIZATION_CANDIDATE", "VIEW", "LOCAL_LEAF", "SYSTEM_OBJECT"
K_BOUNDARY = "BOUNDARY_LEAF"


def is_system_name(name: str) -> bool:
    return name == SYSTEM_NAMESPACE or name.startswith(SYSTEM_NAMESPACE + ".")


class ProfileEvidence:
    """Vista de solo lectura sobre un perfil. ``profile`` no se copia ni se modifica."""

    def __init__(self, profile: Mapping[str, Any]):
        self.profile = profile

    # ───────────────────────────── generales ─────────────────────────────
    @cached_property
    def scope(self) -> Mapping[str, Any]:
        return self.profile.get("scope") or {}

    @cached_property
    def am(self) -> str | None:
        return self.scope.get("terminal_analytic_model")

    @cached_property
    def closure(self) -> frozenset[str]:
        return frozenset(self.scope.get("closure_artifacts") or ())

    @cached_property
    def assessment_state(self) -> str | None:
        return self.profile.get("assessment_state")

    @cached_property
    def versions(self) -> Mapping[str, Any]:
        return self.profile.get("versions") or {}

    @cached_property
    def ce(self) -> Mapping[str, Any]:
        return self.profile.get("canonical_evidence") or {}

    def section(self, name: str) -> Mapping[str, Any]:
        v = self.ce.get(name)
        return v if isinstance(v, Mapping) else {}

    @cached_property
    def has_canonical_evidence_v2(self) -> bool:
        return bool(self.ce) and all(k in self.ce for k in ("closure", "view_complexity", "harmonization_contract", "published_contract"))

    # ───────────────────────────── areas / grupos ─────────────────────────────
    def area(self, area_id: str) -> Mapping[str, Any]:
        for a in self.profile.get("areas") or ():
            if a.get("id") == area_id:
                return a
        return {}

    def area_state(self, area_id: str) -> str | None:
        return self.area(area_id).get("assessability")

    def highlight(self, area_id: str, highlight_id: str) -> Any:
        for h in self.area(area_id).get("highlights") or ():
            if h.get("id") == highlight_id:
                return h.get("value")
        return None

    @cached_property
    def groups(self) -> dict[str, Mapping[str, Any]]:
        out: dict[str, Mapping[str, Any]] = {}
        for g in [*(self.profile.get("observations") or ()), *(self.profile.get("review_required") or ())]:
            out[g.get("id")] = g
        return out

    @cached_property
    def fact_index_names(self) -> frozenset[str]:
        return frozenset(f.get("fact") for f in (self.profile.get("fact_index") or ()) if isinstance(f, Mapping))

    def fact_subjects(self, fact_id: str) -> frozenset[str]:
        prefix = fact_id + "@"
        return frozenset(n[len(prefix):] for n in self.fact_index_names if n.startswith(prefix))

    def surface_state(self, surface_id: str) -> str | None:
        for s in self.profile.get("surfaces") or ():
            if s.get("surface_id") == surface_id:
                return s.get("assessability")
        return None

    @cached_property
    def open_questions(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(((self.profile.get("summary") or {}).get("context_required") or {}).get("questions") or ())

    # ───────────────────────────── vistas y proveedores ─────────────────────────────
    @cached_property
    def views(self) -> dict[str, Mapping[str, Any]]:
        return {v["artifact"]: v for v in (self.section("view_complexity").get("views") or ())}

    @cached_property
    def harmonization(self) -> dict[str, Mapping[str, Any]]:
        return {a["artifact"]: a for a in (self.section("harmonization_contract").get("artifacts") or ())}

    @cached_property
    def local_leaves(self) -> dict[str, Mapping[str, Any]]:
        return {l["leaf"]: l for l in (self.ce.get("leaf_provenance") or ())}

    @cached_property
    def product_nodes(self) -> tuple[str, ...]:
        """Nodos de POSICION de producto: proveedores de linaje directos del modelo analitico (distancia 1). Se define por posicion, no por nombre."""
        v = self.highlight("ANALYTIC_MODEL", "direct_lineage_providers")
        return tuple(v) if isinstance(v, (list, tuple)) else ()

    @cached_property
    def remote_leaves(self) -> frozenset[str]:
        """Hojas con origen remoto importado en el cierre (sujetos del hecho ``source.contract``)."""
        return frozenset(s for s in self.fact_subjects("source.contract") if s in self.closure)

    @cached_property
    def source_state_counts(self) -> dict[str, int]:
        v = self.highlight("SOURCE_EXTRACTION", "by_source_state") or []
        return {x.get("name"): int(x.get("count") or 0) for x in v if isinstance(x, Mapping)}

    @cached_property
    def raw_occurrences(self) -> dict[str, Mapping[str, Any]]:
        """Tablas fisicas ABAP crudas consumidas por Modeling -> ``values`` del hallazgo (espina, distancia, filtro)."""
        g = self.groups.get("DIRECT_RAW_TABLE_ACCESS") or {}
        out: dict[str, Mapping[str, Any]] = {}
        for o in g.get("occurrences") or ():
            for a in o.get("affected_artifacts") or ():
                out[a] = o.get("values") or {}
        return out

    def raw_on_spine(self) -> tuple[str, ...]:
        return tuple(sorted(a for a, v in self.raw_occurrences.items() if v.get("reaches_terminal_by_strict_lineage") is True))

    def classify_source(self, leaf: str, external_policy: str = "UNDETERMINED") -> str | None:
        """Clase del contacto con la fuente de una hoja REMOTA importada (None si no es una hoja remota del cierre).
        Solo se afirma lo que el perfil permite establecer por hoja: cruda (hallazgo explicito) o, si el modelo no tiene ningun origen de clase ``OTHER_ORIGIN``,
        gobernada (CDS / SAPI-ODP / vista estandar). Con ``OTHER_ORIGIN`` presente y sin evidencia por hoja, la hoja queda INDETERMINADA (conservador)."""
        if leaf not in self.remote_leaves:
            return None
        if leaf in self.raw_occurrences:
            return PHYSICAL_RAW_BOUNDARY
        if self.source_state_counts.get("OTHER_ORIGIN", 0) == 0:
            return GOVERNED_SAP_CONTRACT
        return {"UNDETERMINED": EXTERNAL_CONTRACT_UNDETERMINED, "PHYSICAL_RAW": PHYSICAL_RAW_BOUNDARY, "GOVERNED": GOVERNED_SAP_CONTRACT}[external_policy]

    @cached_property
    def has_external_origins(self) -> bool:
        return self.source_state_counts.get("OTHER_ORIGIN", 0) > 0

    # ───────────────────────────── grafo de vistas ─────────────────────────────
    def is_pass_through(self, name: str) -> bool:
        """Vista de paso: 1 proveedor, 1 nodo SELECT, sin joins, uniones, columnas derivadas ni filtros."""
        v = self.views.get(name)
        if not v:
            return False
        m = v.get("metrics") or {}
        f = m.get("filters") or {}
        return (v.get("providers") == 1 and m.get("select_nodes") == 1 and not m.get("joins") and not m.get("unions") and not m.get("derived_columns")
                and not f.get("where_nodes") and not f.get("having_nodes"))

    def collapse(self, name: str) -> str:
        seen = 0
        while name in self.views and name not in self.harmonization and self.is_pass_through(name) and seen < 8:      # un candidato de armonizacion es un nodo de contrato: no se colapsa
            names = self.views[name].get("provider_names") or []
            if len(names) != 1:
                break
            name, seen = names[0], seen + 1
        return name

    def provider_kind(self, name: str) -> str:
        if name in self.harmonization:
            return K_HARM
        if name in self.views:
            return K_VIEW
        if name in self.local_leaves:
            return K_LOCAL_LEAF
        if is_system_name(name):
            return K_SYSTEM
        return K_BOUNDARY

    def effective_providers(self, node: str) -> dict[str, list[str]]:
        """Proveedores directos de ``node`` tras colapsar vistas de paso, agrupados por clase."""
        out: dict[str, list[str]] = {}
        for p in (self.views.get(node) or {}).get("provider_names") or ():
            q = self.collapse(p)
            out.setdefault(self.provider_kind(q), []).append(q)
        return {k: sorted(set(v)) for k, v in sorted(out.items())}

    @cached_property
    def consumers(self) -> dict[str, tuple[str, ...]]:
        rev: dict[str, set[str]] = {}
        for n, v in self.views.items():
            for p in v.get("provider_names") or ():
                rev.setdefault(p, set()).add(n)
        return {k: tuple(sorted(v)) for k, v in rev.items()}

    def descendants(self, name: str) -> tuple[str, ...]:
        seen: set[str] = set()
        dq = deque([name])
        while dq:
            for c in self.consumers.get(dq.popleft(), ()):
                if c not in seen:
                    seen.add(c)
                    dq.append(c)
        return tuple(sorted(seen))

    def view_has_logic(self, name: str) -> bool:
        """El nodo ejecuta responsabilidad logica (joins, uniones, derivaciones o filtros). Se mide por RESPONSABILIDAD, no por estilo grafico/SQL."""
        m = (self.views.get(name) or {}).get("metrics") or {}
        f = m.get("filters") or {}
        return bool(m.get("joins") or m.get("unions") or m.get("derived_columns") or f.get("where_nodes") or f.get("having_nodes"))

    # ───────────────────────────── publicado ─────────────────────────────
    @cached_property
    def published_fields(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(((self.section("published_contract").get("fields") or {}).get("fields")) or ())

    @cached_property
    def published_measures(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(f for f in self.published_fields if f.get("role") == "MEASURE")

    @cached_property
    def business_association_targets(self) -> tuple[str, ...]:
        a = ((self.section("published_contract").get("associations") or {}).get("associations")) or ()
        return tuple(sorted({x.get("target") for x in a if x.get("category") == "BUSINESS_DIMENSION"}))

    @cached_property
    def declared_dimensions_not_associated(self) -> tuple[Any, ...]:
        return tuple(((self.section("published_contract").get("associations") or {}).get("declared_dimensions_in_closure_not_associated")) or ())

    # ───────────────────────────── expresiones / reconvergencia ─────────────────────────────
    @cached_property
    def expression_occurrences(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.section("expression_occurrences").get("occurrences") or ())

    @cached_property
    def reuse_groups(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.section("expression_occurrences").get("reuse_groups") or ())

    @cached_property
    def reconvergences(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.section("branch_reconvergence").get("reconvergences") or ())

    @cached_property
    def assembly_nodes(self) -> dict[str, Mapping[str, Any]]:
        return {a["artifact"]: a for a in (self.section("assembly_topology").get("artifacts") or ())}

    # ───────────────────────────── referencias de evidencia ─────────────────────────────
    def ref_exists(self, ref: str) -> bool:
        """Una ``evidence_ref`` es valida si apunta a algo que EXISTE en el perfil: ``ce:<seccion>[/<clave>]`` o ``fact:<id>@<sujeto>`` o ``area:<ID>``."""
        if ref.startswith("fact:"):
            return ref[5:] in self.fact_index_names
        if ref.startswith("ce:"):
            body = ref[3:]
            sec, _, key = body.partition("/")
            if sec not in self.ce:
                return False
            if not key:
                return True
            return key in self.closure or key in self.views or key in self.harmonization or key in self.local_leaves or key in self.assembly_nodes or self._key_in_section(sec, key)
        if ref.startswith("area:"):
            return bool(self.area(ref[5:]))
        if ref.startswith("group:"):
            return ref[6:] in self.groups
        return False

    def _key_in_section(self, sec: str, key: str) -> bool:
        s = self.ce.get(sec)
        return isinstance(s, Mapping) and key in s

    def known_artifacts(self) -> frozenset[str]:
        return frozenset({*self.closure, *([self.am] if self.am else [])})


def sorted_unique(items: Iterable[str]) -> list[str]:
    return sorted(set(items))
