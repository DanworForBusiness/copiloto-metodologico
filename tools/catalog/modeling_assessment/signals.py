"""Registro versionado de SENALES (M5) — hecho -> senal -> item de revision. Sin juicio de calidad.

Separacion fundamental: FACT -> SIGNAL -> JUDGMENT. M5 implementa solo el tramo FACT -> SIGNAL: detecta que un patron
estructural esta presente y que una revision esta justificada, SIN decir que es malo, debil ni de baja madurez. No hay
score, banda, peso, umbral de madurez, penalidad ni bonus (Fase 1). Cada senal declara que NO debe leerse de ella
(`interpretation_limits`), el contexto que falta (`missing_context`) y las razones legitimas que la explicarian.

Cada entrada del registro (`SignalSpec`) declara: id, area, kind, alcance de rol, hechos requeridos, regla (funcion PURA de
hechos) y textos. Las reglas leen SOLO hechos (`Fact`) ya emitidos por M2-M4: no releen entidades, archivos ni legacy.

Catalogo (P5 canonico):
  OBSERVATION   DIRECT_RAW_TABLE_ACCESS            siempre que exista acceso directo a tabla fisica en el scope Modeling
  REVIEW        PASS_THROUGH_REVIEW                continuidad de schema total + nada introducido (sin juicio; CASE 1 lo contraejemplifica)
                REPEATED_DERIVATION_IN_LINEAGE     mismo campo `derived` en un artefacto y en un ancestro de linaje (+ relacion estructural de cada repeticion; antes CROSS_LAYER_REDERIVATION)
                ROLE_LAYER_DIVERGENCE              capa codificada en el NOMBRE (§28) vs posicion estructural / rol DECLARADO
                DUPLICATED_REMOTE_IMPORT           mismo `remote_entity` bajo >1 identidad local
                LAYER_BOUNDARY_REVIEW              fuente remota cuyos consumidores de linaje estan TODOS en capa inferida >= 3
                FLAT_CENTRAL_ASSEMBLY_REVIEW       ensamble de alto fan-in que declara 0 asociaciones / 0 medidas / 0 dimensiones
                FIELD_VOCABULARY_MIX               etiqueta compartida por nombres tecnicos de FORMAS distintas (sin series numeradas)
                OPAQUE_FIELD_NAME                  nombre con prefijo de namespace propio sin ningun token en comun con su etiqueta
                LABEL_NOT_ENRICHING                todos los campos expuestos tienen etiqueta == nombre tecnico
                AM_SEMANTIC_UNDERUSE_REVIEW        Modelo Analitico terminal sin asociaciones declaradas
  DESCRIPTIVE   SOURCE_FILTER_PRESENT              `remote.query.where` presente (C1); ni bueno ni malo
Los hechos descriptivos LOCAL_CHAIN_REUSE / CROSS_AM_REUSE / familia y estado de fuente / estado SAPI siguen siendo HECHOS
(M2/M3): no generan senal ni polaridad (el reuso no esta calibrado como "mas = mejor").

`SIGNALS_RULESET` versiona las reglas. Los PARAMETROS de una regla son provisionales, estan declarados en su `SignalSpec`
(`parameters`) y no son umbrales de madurez ni de calidad.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import layer_evidence
from .model import (
    Area, Assessability, Fact, Observation, ObservationId, ReviewId, ReviewItem, Signal, SignalKind, _ensure_jsonable,
)

SIGNALS_RULESET = "signal-rules/v4"                       # v2 (M8, parche 1): PASS_THROUGH_REVIEW solo evalua aristas internas modelado->modelado; v3 (G3): CROSS_LAYER_REDERIVATION -> REPEATED_DERIVATION_IN_LINEAGE + relacion estructural; v4 (LAYER_BOUNDARY_REVIEW): evalua el primer consumidor FUNCIONAL (atraviesa vistas de paso), solo con capa respaldada por evidencia (rol declarado / nomenclatura de capas) y sin fuentes de contrato gobernado

_USABLE = (Assessability.ASSESSED, Assessability.PARTIALLY_ASSESSED)

# Parametro PROVISIONAL de FLAT_CENTRAL_ASSEMBLY_REVIEW ("fan-in alto" del catalogo canonico no tiene un corte calibrado).
FLAT_CENTRAL_MINIMUM_FAN_IN = 4

FIELD_MIX_TIER_CO_DECLARED = "CO_DECLARED_IN_ARTIFACT"             # >= 2 de los nombres conviven en UN mismo contrato declarado
FIELD_MIX_TIER_EXPOSED_BY_TERMINAL_AM = "EXPOSED_BY_TERMINAL_AM"   # al menos uno de los nombres llega al contrato del AM terminal


@dataclass(frozen=True)
class Finding:
    """Una deteccion concreta. `detail` es JSON puro y permite verificar y explicar el disparo sin tocar el contrato M0."""
    signal_id: str
    subjects: tuple[str, ...]
    fact_refs: tuple[str, ...]
    detail: dict

    def __post_init__(self) -> None:
        if not self.subjects:
            raise ValueError("Finding sin subjects")
        if not self.fact_refs:
            raise ValueError("Finding sin fact_refs")
        _ensure_jsonable(self.detail, f"finding[{self.signal_id}].detail")


@dataclass(frozen=True)
class SignalSpec:
    signal_id: str
    kind: SignalKind
    area: Area
    role_scope: tuple[str, ...]
    required_fact_ids: tuple[str, ...]
    mapping: str                                   # hecho -> senal, en lenguaje llano (documentacion verificable)
    interpretation_limits: tuple[str, ...]         # que NO debe leerse de esta senal ("non penalties")
    rule: Callable[["FactIndex"], list[Finding]]
    legitimate_reasons: tuple[str, ...] = ()
    missing_context: str = ""
    context_question_key: str | None = None
    parameters: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class SignalEvaluation:
    ruleset_version: str
    signals: tuple[Signal, ...]
    observations: tuple[Observation, ...]
    review_items: tuple[ReviewItem, ...]
    findings: tuple[Finding, ...]


class FactIndex:
    """Indice de hechos independiente del orden de entrada."""

    def __init__(self, facts: Iterable[Fact]) -> None:
        self._by_id: dict[str, list[Fact]] = {}
        for f in sorted(facts, key=lambda f: (f.fact_id, f.subject)):
            self._by_id.setdefault(f.fact_id, []).append(f)

    def all(self, fact_id: str) -> list[Fact]:
        return list(self._by_id.get(fact_id, ()))

    def usable(self, fact_id: str) -> list[Fact]:
        return [f for f in self.all(fact_id) if f.assessability in _USABLE and f.value is not None]

    def get(self, fact_id: str, subject: str) -> Fact | None:
        return next((f for f in self._by_id.get(fact_id, ()) if f.subject == subject), None)

    def usable_get(self, fact_id: str, subject: str) -> Fact | None:
        f = self.get(fact_id, subject)
        return f if f is not None and f.assessability in _USABLE and f.value is not None else None


def _keys(*facts: Fact | None) -> tuple[str, ...]:
    return tuple(f.key for f in facts if f is not None)


def _edge_ends(subject: str) -> tuple[str, str]:
    provider, consumer = subject.split("->", 1)
    return provider, consumer


# ───────────────────────────── reglas (funciones puras de hechos) ─────────────────────────────
def _rule_direct_raw_table_access(idx: FactIndex) -> list[Finding]:
    out = []
    for c in idx.usable("source.contract"):
        if c.value.get("source_state") != "RAW_PHYSICAL_TABLE":
            continue                                             # SIEMPRE que sea tabla fisica: sin condicion de alcance/espina/filtro
        reach = idx.usable_get("source.downstream_reach", c.subject)
        qf = idx.usable_get("source.query_filter", c.subject)
        ident = idx.usable_get("source.identity_continuity", c.subject)
        r = reach.value if reach else {}
        out.append(Finding(
            "DIRECT_RAW_TABLE_ACCESS", (c.subject,), _keys(c, reach, qf, ident, idx.usable_get("source.keys", c.subject)),
            {"remote_entity": c.value.get("remote_entity"), "remote_connection": c.value.get("remote_connection"),
             "artifact_label": c.value.get("artifact_label"), "remote_stem": c.value.get("remote_stem"),
             "identity": ident.value.get("identity") if ident else None,
             "reaches_terminal_by_strict_lineage": r.get("reaches_terminal_by_strict_lineage"),
             "lineage_distance": r.get("lineage_distance"),
             "transitive_lineage_dependents_in_scope": len(r.get("transitive_lineage_dependents_in_scope") or []) if reach else None,
             "source_filter": ("NOT_ASSESSED" if qf is None else ("PRESENT" if qf.value.get("where") else "ABSENT"))}))
    return out


def _rule_source_filter_present(idx: FactIndex) -> list[Finding]:
    return [Finding("SOURCE_FILTER_PRESENT", (f.subject,), (f.key,), {"where": f.value["where"]})
            for f in idx.usable("source.query_filter") if f.value.get("where")]


def _rule_pass_through(idx: FactIndex) -> list[Finding]:
    out = []
    # POBLACION ELEGIBLE (decision del owner, M8): transformaciones INTERNAS modelado->modelado = `propagation.contract_delta`.
    # NO elegibles: fuente/frontera -> primer consumidor (`ingestion.field_retention`) y ultimo proveedor -> AM terminal (`am.contract_delta`).
    for f in idx.usable("propagation.contract_delta"):
        v = f.value
        d = v["descriptors"]
        if (v["consumer_lineage_provider_count"] == 1                          # un consumidor que COMBINA varios proveedores no es pass-through
                and not v["consumer_role"]["declares_dimension"]
                and v["derived"]["consumer_derivation_visible"]                # sin query visible el flag `derived` no existe: no se puede afirmar
                and d["schema_identical"] and d["introduces_no_derived_field"]
                and d["introduces_no_association"] and d["introduces_no_measure"]):
            out.append(Finding("PASS_THROUGH_REVIEW", _edge_ends(f.subject), (f.key,),
                               {"edge": f.subject, "field_count": v["consumer_summary"]["n_fields"],
                                "consumer_declared_pattern": v["consumer_role"]["modeling_pattern"],
                                "provider_inferred_layer": v["provider_layer_inference"]["inferred_layer"],
                                "consumer_inferred_layer": v["consumer_layer_inference"]["inferred_layer"]}))
    return out


def _rule_repeated_derivation(idx: FactIndex) -> list[Finding]:
    """Detecta el PATRON (mismo campo derivado en un artefacto y en un ancestro de linaje). NO afirma duplicacion: cada campo lleva su RELACION
    estructural (DUPLICATED_TRANSFORMATION | CHAINED_REFINEMENT | DISTINCT_TRANSFORMATION | UNKNOWN), calculada en el hecho desde el AST compilado."""
    out = []
    for f in idx.usable("propagation.rederivation"):
        pairs: dict[tuple[str, str], list[dict]] = {}
        for o in f.value["occurrences"]:
            pairs.setdefault((o["provider"], o["consumer"]), []).append(o)
        for (provider, consumer), occ in sorted(pairs.items()):
            out.append(Finding("REPEATED_DERIVATION_IN_LINEAGE", (provider, consumer), (f.key,),
                               {"provider": provider, "consumer": consumer, "fields": sorted(o["field"] for o in occ),
                                "lineage_distance": occ[0]["lineage_distance"], "adjacent": occ[0]["adjacent"],
                                "field_relations": [{"field": o["field"], "relation": o.get("relation", "UNKNOWN"),
                                                     "relation_reason": o.get("relation_reason", "NO_QUERY_EVIDENCE"),
                                                     "exposed_by_root": o.get("exposed_by_root"), "exposure_basis": o.get("exposure_basis")}
                                                    for o in sorted(occ, key=lambda o: o["field"])]}))
    return out


_LAYER_OF_DECLARED_ROLE = (("declares_cube", "4"), ("declares_fact", "3"), ("declares_dimension", "2"))   # misma precedencia que `_capa_of`


def _declared_role_layer(role: dict) -> str | None:
    for flag, layer in _LAYER_OF_DECLARED_ROLE:
        if role.get(flag):
            return layer
    return None


def _rule_role_layer_divergence(idx: FactIndex) -> list[Finding]:
    layer_fact = idx.usable_get("ingestion.inferred_layer_vs_lineage_depth", "closure")
    if layer_fact is None:
        return []
    boundary_fact = idx.usable_get("ingestion.boundary_artifacts", "closure")
    leaves = {a["artifact"] for a in boundary_fact.value["artifacts"]} if boundary_fact else None
    out = []
    for a in layer_fact.value["artifacts"]:
        name = a["artifact"]
        if a["inferred_layer_basis"] != "SECTION_28_NAME":
            continue        # solo cuando el NOMBRE realmente codifica la capa (nunca la capa inferida de rol/prefijo); un nombre SYSTEM (`SAP.*`) nunca la codifica
        layer = a["inferred_layer"]
        naming = idx.usable_get("naming.artifact_name_contract", name)
        kinds = []
        if leaves is not None:
            if name in leaves and layer != "1":
                kinds.append("NAME_LAYER_ABOVE_BOUNDARY_POSITION")      # el nombre dice capa >= 2 pero es una hoja del grafo
            if name not in leaves and layer == "1":
                kinds.append("NAME_LAYER_INGESTION_BUT_NOT_A_BOUNDARY")  # el nombre dice capa 1 pero tiene proveedores
        role_layer = _declared_role_layer(naming.value["declared_role"]) if naming else None
        if role_layer is not None and role_layer != layer:
            kinds.append("NAME_LAYER_DIFFERS_FROM_DECLARED_ROLE_LAYER")
        if kinds:
            out.append(Finding("ROLE_LAYER_DIVERGENCE", (name,), _keys(layer_fact, boundary_fact if leaves is not None else None, naming),
                               {"artifact": name, "name_encoded_layer": layer, "is_boundary_leaf": (name in leaves) if leaves is not None else None,
                                "declared_role_layer": role_layer, "divergence_kinds": kinds}))
    return out


def _rule_duplicated_remote_import(idx: FactIndex) -> list[Finding]:
    out = []
    for f in idx.usable("ingestion.duplicate_remote_imports"):
        for g in f.value["in_universe_touching_closure"]:
            inside = g["in_closure"]
            if not inside:
                continue
            out.append(Finding("DUPLICATED_REMOTE_IMPORT", tuple(inside), (f.key,),
                               {"remote_entity": g["remote_entity"], "local_artifacts": g["local_artifacts"],
                                "in_closure": inside, "spans_outside_closure": len(inside) < len(g["local_artifacts"]),
                                "duplicated_inside_closure": len(inside) > 1}))
    return out


def _numeric_layer(layer: str) -> int | None:
    return int(layer) if str(layer).isdigit() and layer != "9" else None


_MAX_PASS_THROUGH_DEPTH = 8                    # misma cota que el colapso de vistas de paso del Juicio


def _lineage_edges_between_modeled_artifacts(idx: FactIndex) -> dict[str, dict[str, tuple[dict, Fact]]]:
    """proveedor -> {consumidor: (capa inferida del consumidor, hecho de la arista)} para las aristas de linaje entre artefactos modelados (incluida la que llega al modelo analitico)."""
    out: dict[str, dict[str, tuple[dict, Fact]]] = {}
    for fact_id in ("propagation.contract_delta", "am.contract_delta"):
        for f in idx.usable(fact_id):
            provider, consumer = _edge_ends(f.subject)
            out.setdefault(provider, {})[consumer] = (f.value.get("consumer_layer_inference") or {}, f)
    return out


def _functional_consumers(consumer: str, layer: dict, views: dict[str, Fact], edges: dict[str, dict[str, tuple[dict, Fact]]],
                          via: tuple[str, ...] = ()) -> tuple[list[dict], list[Fact]] | None:
    """Primer consumidor FUNCIONALMENTE relevante a partir de ``consumer``: si es una vista de paso (``layer_evidence.is_pass_through_view``, la MISMA definicion que usa el Juicio)
    se atraviesa hacia sus consumidores de linaje; si no, es el propio consumidor. ``None`` = linaje incompleto o ambiguo (vista de paso sin consumidor conocido o cadena demasiado larga):
    no se afirma nada."""
    view = views.get(consumer)
    if not layer_evidence.is_pass_through_view(view.value if view is not None else None):
        return [{"consumer": consumer, "inferred_layer": layer.get("inferred_layer"), "inferred_layer_basis": layer.get("inferred_layer_basis"), "reached_through": list(via)}], \
            ([view] if view is not None else [])
    nxt = edges.get(consumer)
    if not nxt or len(via) >= _MAX_PASS_THROUGH_DEPTH:
        return None
    found: list[dict] = []
    used: list[Fact] = [view]
    for name in sorted(nxt):
        sub = _functional_consumers(name, nxt[name][0], views, edges, (*via, consumer))
        if sub is None:
            return None
        found.extend(sub[0])
        used.extend([nxt[name][1], *sub[1]])
    return found, used


def _rule_layer_boundary(idx: FactIndex) -> list[Finding]:
    """Fuente remota cuyo primer consumidor FUNCIONAL ya esta en capa >= 3. Tres condiciones de rigor (signal-rules/v4):
      (a) el consumidor inmediato puede ser una vista de paso que solo proyecta la fuente: se atraviesa y se evalua el primer consumidor que no es de paso;
      (b) la capa de cada consumidor funcional debe estar RESPALDADA POR EVIDENCIA (rol declarado o nomenclatura explicita de capas): ``NAME_PREFIX``/``UNKNOWN`` no alcanzan;
      (c) una fuente con contrato de origen gobernado (``source.contract_class``) no genera esta revision solo por carecer de una frontera intermedia de armonizacion (sigue sujeta a las demas reglas).
    Sin evidencia suficiente (linaje incompleto, capa no respaldada) NO se emite: la regla nunca fabrica ni asume una capa."""
    boundary_fact = idx.usable_get("ingestion.boundary_artifacts", "closure")
    if boundary_fact is None:
        return []
    remote_leaves = {a["artifact"] for a in boundary_fact.value["artifacts"] if a["has_remote_entity"]}
    views = {f.subject: f for f in idx.usable("harmonization.view_complexity")}
    edges = _lineage_edges_between_modeled_artifacts(idx)
    out = []
    for f in idx.usable("ingestion.first_consumers"):
        if f.subject not in remote_leaves:
            continue
        cons = f.value["lineage_consumers"]
        if not cons:
            continue
        contract_class = idx.usable_get("source.contract_class", f.subject)
        if contract_class is not None and contract_class.value.get("contract_class") in layer_evidence.GOVERNED_CONTRACT_CLASSES:
            continue                                                       # (c) la pregunta «¿falta armonizacion?» ya esta respondida por el contrato de origen gobernado
        functional: list[dict] = []
        used: list[Fact] = []
        for c in cons:
            sub = _functional_consumers(c["consumer"], c, views, edges)
            if sub is None:
                functional = []
                break
            functional.extend(sub[0])
            used.extend(sub[1])
        if not functional:
            continue                                                       # linaje incompleto/ambiguo: no se afirma
        if not all(c["inferred_layer_basis"] in layer_evidence.LAYER_EVIDENCE_BASES and (_numeric_layer(c["inferred_layer"]) or 0) >= 3 for c in functional):
            continue                                                       # (b) la capa del consumidor funcional no esta respaldada por evidencia, o es < 3
        immediate = sorted({c["consumer"] for c in cons})
        functional = sorted({(c["consumer"], c["inferred_layer"], c["inferred_layer_basis"], tuple(c["reached_through"])) for c in functional})
        subjects = (f.subject, *sorted({*immediate, *(c[0] for c in functional)}))
        out.append(Finding("LAYER_BOUNDARY_REVIEW", subjects, tuple(dict.fromkeys(_keys(f, boundary_fact, contract_class, *sorted(used, key=lambda x: x.key)))),
                           {"source": f.subject,
                            "lineage_consumers": [{"consumer": c["consumer"], "inferred_layer": c["inferred_layer"], "inferred_layer_basis": c["inferred_layer_basis"]} for c in cons],
                            "functional_consumers": [{"consumer": n, "inferred_layer": layer, "inferred_layer_basis": basis, "reached_through": list(through)}
                                                     for n, layer, basis, through in functional],
                            "association_consumer_count": len(f.value["association_consumers"])}))
    return out


def _rule_flat_central_assembly(idx: FactIndex) -> list[Finding]:
    out = []
    for f in idx.usable("harmonization.composition_artifacts"):
        for a in f.value["artifacts"]:
            if (a["declares_no_associations_measures_dimensions"] and not a["declared_role"]["declares_dimension"]
                    and a["lineage_provider_count"] >= FLAT_CENTRAL_MINIMUM_FAN_IN):
                out.append(Finding("FLAT_CENTRAL_ASSEMBLY_REVIEW", (a["artifact"],), (f.key,),
                                   {"artifact": a["artifact"], "fan_in": a["lineage_provider_count"],
                                    "boundary_providers": sum(1 for p in a["lineage_providers"] if p["is_boundary_artifact"]),
                                    "lineage_consumer_count": a["lineage_consumer_count"],
                                    "declared_pattern": a["declared_role"]["modeling_pattern"],
                                    "minimum_fan_in": FLAT_CENTRAL_MINIMUM_FAN_IN}))
    return out


def _rule_field_vocabulary_mix(idx: FactIndex) -> list[Finding]:
    same = idx.usable_get("naming.same_normalized_label_multiple_technical_names", "closure")
    if same is None:
        return []
    forms: dict[tuple[str, str], str] = {}
    forms_facts: dict[str, Fact] = {}
    for f in idx.usable("naming.field_name_forms"):
        forms_facts[f.subject] = f
        for r in f.value["fields"]:
            forms[(f.subject, r["field"])] = r["form"]
    out = []
    for g in same.value["groups"]:
        if g["is_numbered_series"]:
            continue                                                  # las series numeradas NO son un mix de vocabulario (se conservan en el hecho M4)
        per_name = {t["field"]: sorted({forms[(a, t["field"])] for a in t["artifacts"] if (a, t["field"]) in forms}) for t in g["technical_names"]}
        distinct_forms = sorted({x for v in per_name.values() for x in v})
        if len(distinct_forms) < 2:
            continue                                                  # igual etiqueta y misma forma de nombre: no hay evidencia de vocabularios distintos
        co = g["artifacts_declaring_multiple_names"]
        tiers = ([FIELD_MIX_TIER_CO_DECLARED] if co else []) + ([FIELD_MIX_TIER_EXPOSED_BY_TERMINAL_AM] if g["terminal_am_fields"] else [])
        if not tiers:
            continue                                                  # solo evidencia FUERTE: el resto queda como hecho de M4, sin item de revision
        arts = sorted({a for t in g["technical_names"] for a in t["artifacts"]})
        out.append(Finding(
            "FIELD_VOCABULARY_MIX", tuple(arts), (same.key, *(forms_facts[a].key for a in arts if a in forms_facts)),
            {"label": g["label"], "technical_names": [t["field"] for t in g["technical_names"]], "distinct_forms": distinct_forms,
             "forms_by_name": [{"field": k, "forms": v} for k, v in sorted(per_name.items())],
             "evidence_tiers": tiers, "co_declared_in": [c["artifact"] for c in co], "terminal_am_fields": g["terminal_am_fields"],
             "occurrence_count": g["occurrence_count"]}))
    return out


def _fold(text: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c)).lower()


def _tokens(text: str, *, split_underscore: bool) -> list[str]:
    parts = re.split(r"[_\d]+|[^a-z0-9]+", _fold(text)) if split_underscore else re.findall(r"[a-z]+", _fold(text))
    return [p for p in parts if len(p) >= 3]


def _share_a_token(name_tokens: list[str], label_tokens: list[str]) -> bool:
    return any(a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a))) for a in name_tokens for b in label_tokens)


def _rule_opaque_field_name(idx: FactIndex) -> list[Finding]:
    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for forms in idx.usable("naming.field_name_forms"):
        contract = idx.usable_get("naming.field_name_contract", forms.subject)
        if contract is None:
            continue
        rel = {r["field"]: r for r in contract.value["fields"] if r["label_present"]}
        for r in forms.value["fields"]:
            lab = rel.get(r["field"])
            if not r["custom_namespace_prefix"] or lab is None:
                continue
            name_tokens = _tokens(r["field"], split_underscore=True)[1:]      # sin el token del prefijo de namespace propio
            if not name_tokens or _share_a_token(name_tokens, _tokens(lab["label"], split_underscore=False)):
                continue                                              # sin tokens comparables, o el nombre ya comparte vocabulario con la etiqueta
            h = hits.setdefault((r["field"], lab["label"]), {"artifacts": [], "refs": []})
            h["artifacts"].append(forms.subject)
            h["refs"].extend(_keys(forms, contract))
    # un item por (campo, etiqueta): el mismo campo opaco se propaga por varias capas y no debe repetirse por artefacto
    return [Finding("OPAQUE_FIELD_NAME", tuple(sorted(h["artifacts"])), tuple(sorted(set(h["refs"]))),
                    {"field": fname, "label": label, "exposed_in": sorted(h["artifacts"])})
            for (fname, label), h in sorted(hits.items())]


def _rule_label_not_enriching(idx: FactIndex) -> list[Finding]:
    out = []
    for f in idx.usable("naming.field_name_contract"):
        v = f.value
        if v["n_fields"] >= 1 and v["label_equals_field"] == v["n_fields"]:       # implica que ningun campo esta sin etiqueta
            out.append(Finding("LABEL_NOT_ENRICHING", (f.subject,), (f.key,), {"artifact": f.subject, "n_fields": v["n_fields"],
                                                                              "label_equals_field": v["label_equals_field"]}))
    return out


def _rule_am_semantic_underuse(idx: FactIndex) -> list[Finding]:
    out = []
    for f in idx.usable("am.contract_inventory"):
        if not f.value["associations"]:
            out.append(Finding("AM_SEMANTIC_UNDERUSE_REVIEW", (f.subject,), (f.key,),
                               {"n_associations": 0, "n_measures": len(f.value["measures"]),
                                "n_declared_dimensions": len(f.value["declared_dimensions"]),
                                "label_coverage": f.value["label_coverage"]}))
    return out


# ───────────────────────────── registro ─────────────────────────────
SIGNAL_REGISTRY: tuple[SignalSpec, ...] = (
    SignalSpec(
        ObservationId.DIRECT_RAW_TABLE_ACCESS.value, SignalKind.OBSERVATION, Area.SOURCE_EXTRACTION, ("remote_bearing_artifact",),
        ("source.contract",),
        "source.contract@X.source_state == RAW_PHYSICAL_TABLE -> one observation per artifact, ALWAYS (independent of strict-lineage reach, "
        "spine position, source filter or label); reach/dependents/filter/label/identity are attached as context facts",
        ("a direct physical-table access is not by itself an invalid architecture",
         "no fixed penalty and no severity: reach, dependents, filter and label are context, not weights",
         "resolution stays OPEN_CONTEXT_REQUIRED until the project context is known"),
        _rule_direct_raw_table_access,
        missing_context="Is the direct physical-table access a justified methodological exception for this artifact, or a deviation?",
        context_question_key="direct_raw_table_access_justified_exception"),
    SignalSpec(
        "SOURCE_FILTER_PRESENT", SignalKind.DESCRIPTIVE, Area.SOURCE_EXTRACTION, ("remote_bearing_artifact",),
        ("source.query_filter",), "source.query_filter@X.where is non-empty -> descriptive signal (evidence class C1)",
        ("a source filter is neither good nor bad", "the loader does not consume remote.query: the evidence is a supplementary C1 read"),
        _rule_source_filter_present),
    SignalSpec(
        ReviewId.PASS_THROUGH_REVIEW.value, SignalKind.REVIEW, Area.PROPAGATION, ("non_dimension_consumer",),
        ("propagation.contract_delta",),
        "an INTERNAL modeled transformation edge (modeled artifact -> modeled artifact; never source -> first consumer, never final provider -> terminal model) whose consumer has exactly one lineage provider, is not a declared dimension, exposes a visible derivation flag and "
        "has identical schema + no introduced derived field / association / measure -> review item for that edge",
        ("pass-through is not low maturity", "a near-pure pass-through can be methodologically acceptable (CASE 1 is the counterexample)",
         "the reading depends on the functional role of the layer",
         "no signal does NOT mean the pattern is absent: internal edges without a visible query or descriptors are not evaluable; source-to-first-consumer and final-provider-to-terminal-model edges are outside this rule's population"),
        _rule_pass_through,
        legitimate_reasons=("serving boundary", "product boundary", "stable semantic contract", "technical decomposition", "compatibility layer"),
        missing_context="Functional role of the consuming layer (serving or product boundary, stable contract, technical decomposition, compatibility)."),
    SignalSpec(
        ReviewId.REPEATED_DERIVATION_IN_LINEAGE.value, SignalKind.REVIEW, Area.PROPAGATION, ("artifact_with_visible_query",),
        ("propagation.rederivation",),
        "propagation.rederivation occurrences grouped by (provider, consumer) -> one review item per pair (adjacent or later lineage layers)",
        ("not an automatic defect (contract stability review)", "it does not say which derivation is the right one",
         "a repeated field name alone does not mean duplicated logic: each occurrence carries a structural relation read from the compiled query (DUPLICATED_TRANSFORMATION | CHAINED_REFINEMENT | DISTINCT_TRANSFORMATION | UNKNOWN)",
         "CHAINED_REFINEMENT (the downstream expression reads the already-derived upstream field) is not a deviation; business semantics of formulas are not inferred"),
        _rule_repeated_derivation,
        legitimate_reasons=("a unit, currency or type conversion deliberately re-applied", "a different aggregation level per layer",
                            "recomputation required by a later join or filter", "intentional contract hardening at a boundary"),
        missing_context="Whether the field is intentionally recomputed at each layer, and which layer owns its definition."),
    SignalSpec(
        ReviewId.ROLE_LAYER_DIVERGENCE.value, SignalKind.REVIEW, Area.INGESTION, ("artifact_whose_name_encodes_a_layer",),
        ("ingestion.inferred_layer_vs_lineage_depth", "ingestion.boundary_artifacts", "naming.artifact_name_contract"),
        "only when the layer is encoded by the section 28 NAME: a boundary leaf named layer >= 2, a non-leaf named layer 1, or a name layer that "
        "differs from the layer of the DECLARED role (cube 4 / fact 3 / dimension 2) -> review item for the artifact",
        ("not a naming defect by itself", "the name-encoded layer is a project convention, not proven responsibility",
         "no role is declared incorrect"),
        _rule_role_layer_divergence,
        legitimate_reasons=("materialized staging object named after a later layer", "layer digit follows a project convention",
                            "declared pattern reflects tool defaults"),
        missing_context="Which layer the artifact is meant to serve in this project."),
    SignalSpec(
        ReviewId.DUPLICATED_REMOTE_IMPORT.value, SignalKind.REVIEW, Area.INGESTION, ("remote_bearing_artifact",),
        ("ingestion.duplicate_remote_imports",),
        "a remote entity imported under more than one local identity, with at least one identity inside the closure -> one review item per remote entity",
        ("not an automatic defect", "multiple identities can be legitimate", "scope (inside closure or across the client universe) is context, not severity"),
        _rule_duplicated_remote_import,
        legitimate_reasons=("parallel ECC and S/4 sources during a migration", "different filters or projections per consumer",
                            "separate security or refresh needs", "copies kept per business domain"),
        missing_context="Reason for importing the same remote entity under several local identities."),
    SignalSpec(
        ReviewId.LAYER_BOUNDARY_REVIEW.value, SignalKind.REVIEW, Area.INGESTION, ("remote_bearing_artifact",),
        ("ingestion.first_consumers", "ingestion.boundary_artifacts", "harmonization.view_complexity", "propagation.contract_delta", "am.contract_delta", "source.contract_class"),
        "a remote-bearing boundary leaf WITHOUT a governed source contract whose first FUNCTIONAL lineage consumers (looking through pass-through views that only project the source) "
        "are ALL at layer >= 3 backed by evidence (declared role or explicit section 28 layer naming; NAME_PREFIX / UNKNOWN never suffice) -> one review item per leaf",
        ("not a penalty per occurrence", "the layer is not proven responsibility", "a direct shortcut can be legitimate",
         "the immediate consumer can be a pass-through view that only projects the source: the functional consumer is reported separately and no direct connection to it is implied",
         "no signal when the lineage is incomplete or the layer is not backed by evidence"),
        _rule_layer_boundary,
        legitimate_reasons=("source already harmonized (for example a governed CDS view)", "reference or text table consumed once",
                            "harmonization intentionally performed in the consuming view"),
        missing_context="Whether harmonization is intentionally absent for this source."),
    SignalSpec(
        ReviewId.FLAT_CENTRAL_ASSEMBLY_REVIEW.value, SignalKind.REVIEW, Area.HARMONIZATION, ("composition_artifact_not_declared_dimension",),
        ("harmonization.composition_artifacts",),
        "a composition artifact (fan-in >= 2) that declares 0 associations, 0 measures and 0 dimensions, is not a declared dimension and has fan-in >= "
        "FLAT_CENTRAL_MINIMUM_FAN_IN (provisional) -> review item",
        ("a flat central assembly is not necessarily poor design", "fan-in alone is not evidence of a defect",
         "the minimum fan-in is a provisional rule parameter, not a maturity threshold",
         "minimum fan-in is provisional shadow detection logic and must be validated during shadow replay / M8 before being treated as a stable review rule"),
        _rule_flat_central_assembly,
        legitimate_reasons=("technical integration view whose semantics live downstream", "contract declared in the consuming layer"),
        missing_context="Whether the semantic contract is intentionally declared downstream of this assembly.",
        parameters=(("minimum_fan_in", FLAT_CENTRAL_MINIMUM_FAN_IN),)),
    SignalSpec(
        ReviewId.FIELD_VOCABULARY_MIX.value, SignalKind.REVIEW, Area.NAMING, ("field",),
        ("naming.same_normalized_label_multiple_technical_names", "naming.field_name_forms"),
        "a same-normalized-label group that is NOT a numbered series, whose technical names have at least two distinct mechanical forms AND that has strong "
        "evidence (names co-declared in one artifact, or a name exposed by the terminal analytic model) -> one review item per group; weaker "
        "cross-artifact groups stay as M4 facts",
        ("a shared label is evidence, not proof, that the names denote the same business concept",
         "not a synonym, inconsistency or duplication finding", "numbered column series are excluded",
         "generic labels may still produce false-positive review candidates: no semantic evidence separates a synonym from a reused label"),
        _rule_field_vocabulary_mix,
        legitimate_reasons=("different source vocabularies kept as delivered", "a generic label reused for different concepts",
                            "vocabulary unification planned downstream"),
        missing_context="Whether the technical names denote the same business concept and whether vocabulary unification is expected."),
    SignalSpec(
        ReviewId.OPAQUE_FIELD_NAME.value, SignalKind.REVIEW, Area.NAMING, ("field",),
        ("naming.field_name_forms", "naming.field_name_contract"),
        "a field with a custom-namespace prefix (Z...), comparable name tokens (none when the name is too short; a label equal to the name always "
        "shares them) and no token in common with its label (accent/case-folded, prefix token ignored) -> one review item per (field, label) "
        "listing every artifact that exposes it",
        ("readability of a name is not assessed without a dictionary: only this narrow, mechanical pattern is detected",
         "the label may be the authoritative business term", "not a judgment on the label",
         "intentionally limited by evidence: no enterprise vocabulary dictionary exists, so opaque names without a custom-namespace prefix are not detected"),
        _rule_opaque_field_name,
        legitimate_reasons=("name inherited from the source system", "established internal acronym", "the label is the governed business term"),
        missing_context="Whether the technical name is meant to be self-explanatory or the label is the governed term."),
    SignalSpec(
        ReviewId.LABEL_NOT_ENRICHING.value, SignalKind.REVIEW, Area.NAMING, ("artifact_with_exposed_fields",),
        ("naming.field_name_contract",),
        "an artifact whose every exposed field has a label (none absent) exactly equal to its technical name -> review item",
        ("a label equal to the technical name is not a defect by itself", "applies only when EVERY exposed field repeats its name"),
        _rule_label_not_enriching,
        legitimate_reasons=("technical staging structure not exposed to consumers", "labels intentionally deferred to the consuming layer"),
        missing_context="Whether this artifact is meant to expose business labels."),
    SignalSpec(
        ReviewId.AM_SEMANTIC_UNDERUSE_REVIEW.value, SignalKind.REVIEW, Area.ANALYTIC_MODEL, ("terminal_analytic_model",),
        ("am.contract_inventory",),
        "the terminal analytic model declares no associations -> review item (context dependent)",
        ("an analytic model without associations is not universally deficient",
         "texts and hierarchies are not observable beyond the declared associations"),
        _rule_am_semantic_underuse,
        legitimate_reasons=("consumed by a tool that provides its own dimensions", "flat reporting extract by design",
                            "semantics modeled in a downstream layer"),
        missing_context="Whether the analytic model is intentionally flat."),
)


def evaluate_signals(facts: Iterable[Fact]) -> SignalEvaluation:
    """Aplica TODO el registro a un conjunto de hechos. Pura, determinista e independiente del orden de entrada."""
    idx = FactIndex(facts)
    signals: list[Signal] = []
    observations: list[Observation] = []
    reviews: list[ReviewItem] = []
    all_findings: list[Finding] = []
    for spec in SIGNAL_REGISTRY:
        findings = sorted(spec.rule(idx), key=lambda f: (f.subjects, f.fact_refs, repr(sorted(f.detail.items()))))
        if not findings:
            continue
        all_findings.extend(findings)
        signals.append(Signal(spec.signal_id, spec.area, spec.kind, spec.role_scope,
                              tuple(sorted({r for f in findings for r in f.fact_refs})), spec.interpretation_limits, SIGNALS_RULESET))
        for f in findings:
            if spec.kind is SignalKind.OBSERVATION:
                observations.append(Observation(ObservationId(spec.signal_id), spec.area, f.subjects, f.fact_refs,
                                                spec.context_question_key or spec.signal_id.lower()))
            elif spec.kind is SignalKind.REVIEW:
                reviews.append(ReviewItem(ReviewId(spec.signal_id), spec.area, f.subjects, f.fact_refs, spec.missing_context,
                                          spec.legitimate_reasons))
    return SignalEvaluation(SIGNALS_RULESET, tuple(signals), tuple(observations), tuple(reviews), tuple(all_findings))
