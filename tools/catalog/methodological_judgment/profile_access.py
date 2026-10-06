"""Acceso de SOLO LECTURA, tipado, al perfil PUBLICO persistido de un Assessment (``profile_json``).

Existe para que los CONSUMIDORES HERMANOS de la evidencia canonica (el motor de Juicio metodologico y, mas adelante, un motor de Clasificacion de Dominio) lean el
mismo perfil sin duplicar la logica de extraccion. NO calcula juicio, NO decide calidad, NO escribe, NO toca el filesystem ni la red y NUNCA muta el perfil.

Todo lo derivado aqui sale del perfil persistido (``canonical_evidence`` v3 (v2 + linaje de campo publicado + clase de contrato de fuente por hoja) + ``areas`` + ``review_required``/``observations`` + ``fact_index`` + ``scope``): no hay
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
_GOVERNED_CONTRACT_CLASSES = frozenset({"GOVERNED_SAP_CDS_CONTRACT", "GOVERNED_SAP_ODP_SAPI_CONTRACT", "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE", "SAP_BW_BRIDGE_VIRTUALIZATION"})
EXTERNAL_POLICIES = ("UNDETERMINED", "PHYSICAL_RAW", "GOVERNED")                     # solo para PRUEBAS de sensibilidad; el motor usa UNDETERMINED

# Sufijo de la tabla FISICA de captura de cambios (delta-shadow) de Datasphere. ESPEJO DELIBERADO de la constante
# canónica ``readers.DELTA_SHADOW_SUFFIX`` del paquete de evidencia: ese paquete está aislado por los invariantes de
# frontera (R-I2/R-I3) y este paquete HERMANO de Juicio no puede importarlo. La equivalencia se blinda con un test de
# contrato (``test_methodological_judgment_identity::test_delta_shadow_suffix_mirrors_the_canonical_constant``).
_DELTA_SHADOW_SUFFIX = "_Delta"
# Valor de ``target_match`` en la procedencia de hoja (``leaf_provenance.producer_flows``) que PRUEBA, por evidencia
# factual, que el productor de la hoja lógica apunta a su tabla sombra de captura de cambios. Espejo del valor canónico
# del contrato de evidencia (``TARGET_MATCHES``). La identidad delta-shadow se resuelve SOLO ante este hecho, NUNCA por
# el string "_Delta" (una entidad ``X_Delta`` modelada de forma independiente no tiene este hecho).
_TARGET_MATCH_DELTA_SHADOW = "DELTA_SHADOW_SUFFIX"

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

    @cached_property
    def has_canonical_evidence_v3(self) -> bool:
        """El Juicio v1 se evalua SOBRE ``canonical-evidence/v3``: sin linaje de campo ni clase de contrato por hoja no se puede establecer causalmente la restauracion semantica."""
        return self.has_canonical_evidence_v2 and self.ce.get("version") == "canonical-evidence/v3" and "source_contract_classes" in self.ce and "published_field_lineage" in self.ce

    # ───────────────────────────── clase de contrato de fuente por hoja y linaje de campo (v3) ─────────────────────────────
    @cached_property
    def leaf_class_records(self) -> dict[str, Mapping[str, Any]]:
        return {r["leaf"]: r for r in (self.section("source_contract_classes").get("leaves") or ())}

    @cached_property
    def lineage(self) -> Mapping[str, Any] | None:
        v = self.ce.get("published_field_lineage")
        return v if isinstance(v, Mapping) else None

    @cached_property
    def lineage_fields(self) -> dict[str, Mapping[str, Any]]:
        return {r["technical_name"]: r for r in ((self.lineage or {}).get("fields") or ())}

    @cached_property
    def lineage_incomplete_fields(self) -> tuple[str, ...]:
        return tuple(sorted(n for n, r in self.lineage_fields.items() if r.get("completeness") != "COMPLETE"))

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

    def _has_delta_shadow_producer(self, leaf: str) -> bool:
        """¿La EVIDENCIA factual de procedencia de la hoja lógica ``leaf`` prueba que su productor apunta a la tabla
        sombra de captura de cambios? (``leaf_provenance[leaf].producer_flows[].target_match == DELTA_SHADOW_SUFFIX``).
        Es el hecho que distingue una sombra técnica de una entidad ``X_Delta`` modelada de forma independiente."""
        rec = self.local_leaves.get(leaf)
        if not isinstance(rec, Mapping):
            return False
        return any(isinstance(pf, Mapping) and pf.get("target_match") == _TARGET_MATCH_DELTA_SHADOW
                   for pf in (rec.get("producer_flows") or ()))

    def _leaf_class_record(self, leaf: str) -> Mapping[str, Any] | None:
        """Registro de clase de contrato de la hoja frontera ``leaf``, resolviendo la IDENTIDAD delta-shadow: una fuente
        lógica ``X`` puede estar registrada en ``source_contract_classes`` bajo su nombre FÍSICO de captura de cambios
        ``X_Delta`` (el destino del Replication Flow). Coincidencia EXACTA primero; si falla, se resuelve ``X_Delta``
        SOLO si la procedencia factual de ``X`` prueba un productor DELTA_SHADOW_SUFFIX (no por el string "_Delta").
        Misma identidad canónica ``X_Delta <-> X`` que alineó la adquisición en Gate A.

        SEGURIDAD: solo REDIRIGE la identidad a un registro YA clasificado; NUNCA fabrica clase (devuelve la clase real
        del registro delta-shadow, incluida ``UNKNOWN`` → indeterminada). La precedencia exacta garantiza que si ``X`` es
        una entidad propia registrada se usa su registro; y una entidad ``X_Delta`` INDEPENDIENTE (sin el hecho de
        procedencia delta-shadow para ``X``) NUNCA se colapsa a ``X``."""
        rec = self.leaf_class_records.get(leaf)
        if rec is not None:
            return rec
        if leaf.endswith(_DELTA_SHADOW_SUFFIX):                      # no re-normalizar un nombre que ya es la sombra
            return None
        if not self._has_delta_shadow_producer(leaf):               # sin PRUEBA factual de sombra técnica: no se resuelve
            return None
        return self.leaf_class_records.get(leaf + _DELTA_SHADOW_SUFFIX)

    def classify_source(self, leaf: str, external_policy: str = "UNDETERMINED") -> str | None:
        """Clase del contacto con la fuente de una hoja frontera, SEGUN LA EVIDENCIA EXPLICITA por hoja (``source_contract_classes``): CDS / ODP-SAPI / vista estandar / puente BW =>
        gobernada; tabla ABAP fisica => frontera fisica cruda; fuente externa / mixta => indeterminada; DESCONOCIDA (procedencia no evaluada) => indeterminada; plataforma => None
        (no es un contacto con la fuente). ``external_policy`` existe SOLO para pruebas de sensibilidad y solo afecta a las clases EXTERNAL_*/MIXED (nunca a las desconocidas)."""
        rec = self._leaf_class_record(leaf)
        if rec is None:
            return None
        cls = rec["contract_class"]
        if cls == "PLATFORM_PROVIDED":
            return None
        if cls in _GOVERNED_CONTRACT_CLASSES:
            return GOVERNED_SAP_CONTRACT
        if cls == "PHYSICAL_ABAP_TABLE_BOUNDARY":
            return PHYSICAL_RAW_BOUNDARY
        if cls == "UNKNOWN":
            return EXTERNAL_CONTRACT_UNDETERMINED
        return {"UNDETERMINED": EXTERNAL_CONTRACT_UNDETERMINED, "PHYSICAL_RAW": PHYSICAL_RAW_BOUNDARY, "GOVERNED": GOVERNED_SAP_CONTRACT}[external_policy]

    @cached_property
    def source_contact_leaves(self) -> tuple[str, ...]:
        """Hojas frontera que son un CONTACTO con la fuente (todas menos las de plataforma)."""
        return tuple(sorted(l for l, r in self.leaf_class_records.items() if r["contract_class"] != "PLATFORM_PROVIDED"))

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

    def ancestors(self, name: str) -> tuple[str, ...]:
        """Todo lo que esta aguas ARRIBA de ``name`` (proveedores transitivos dentro del cierre)."""
        seen: set[str] = set()
        dq = deque([name])
        while dq:
            for p in (self.views.get(dq.popleft()) or {}).get("provider_names") or ():
                if p not in seen:
                    seen.add(p)
                    dq.append(p)
        return tuple(sorted(seen))

    def bypass_path_nodes(self, leaf: str) -> tuple[str, ...]:
        """Nodos intermedios de los caminos ``leaf -> nodo de producto`` que NO atraviesan ningun candidato de armonizacion (la hoja evita/omite la frontera reutilizable).
        Vacio si todo camino pasa por un candidato o no alcanza el producto."""
        prod = set(self.product_nodes)
        fwd: set[str] = set()
        dq = deque([leaf])
        while dq:
            n = dq.popleft()
            for c in self.consumers.get(n, ()):
                if c in self.harmonization or c in fwd:
                    continue
                fwd.add(c)
                dq.append(c)
        if leaf in prod:
            return ()
        back: set[str] = set(n for n in fwd if n in prod)
        dq = deque(back)
        while dq:
            n = dq.popleft()
            for p in (self.views.get(n) or {}).get("provider_names") or ():
                if p in fwd and p not in back:
                    back.add(p)
                    dq.append(p)
        return tuple(sorted(back))

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
        if sec == "published_field_lineage":
            return key in self.lineage_fields
        if sec == "source_contract_classes":
            return key in self.leaf_class_records
        s = self.ce.get(sec)
        return isinstance(s, Mapping) and key in s

    def known_artifacts(self) -> frozenset[str]:
        return frozenset({*self.closure, *([self.am] if self.am else [])})


def sorted_unique(items: Iterable[str]) -> list[str]:
    return sorted(set(items))
