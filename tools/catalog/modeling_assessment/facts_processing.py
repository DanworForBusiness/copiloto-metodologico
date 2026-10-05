"""Propiedades de CARGA de los flujos que producen hojas del cierre (S1/S7-lectura, evidence release). Solo lee lo que el export YA trae. Sin juicio.

``ingestion.flow_load_properties`` (subject = ``closure``, plano PROCESSING, evidencia COMPLEMENTARIA: nunca altera denominadores de Modeling):
  * Data Flow: modo de escritura (``mode``: append/truncate), ``upsert``, ``batchSize``, tipo del objeto fuente, suscripcion de lectores ABAP CDS, y el filtro VERBATIM
    de la vista fuente cuando el flujo lee una vista (``SELECT.where`` del SELECT externo, tal cual se exporta);
  * Replication Flow: ``loadType``, ``truncate``, ``deltaLoadTrigger``, ``deltaCheckInterval``, ``isDeltaDisabled``, filtros de transformacion y propiedades CDC;
  * Transformation Flow: ``loadType``, ``deltaEnabled``, ``truncate`` y las anotaciones ``@DataWarehouse.delta``.
Los valores son EXACTAMENTE los exportados. Lo que el export NO trae queda declarado como no observado (``unobserved_properties``): calendarizacion,
estado federado/replicado de las tablas remotas, modo de ejecucion. NUNCA se infiere FULL / DELTA / incremental / federado / replicado: ni de ``append``, ni de
``upsert``, ni de un filtro de fecha. ``inferences`` queda vacio por contrato.
"""
from __future__ import annotations

from typing import Any, Mapping

from .facts_support import make_fact, sort_facts
from .identity import IdentityClassification, classify
from .model import Area, EvidenceClass, EvidenceRef, Fact, Plane
from .readers import DATA_FLOW, DELTA_SHADOW_SUFFIX, REPLICATION_FLOW, TRANSFORMATION_FLOW
from .scope import AssessmentUniverse

ING_ = Area.INGESTION
FLOW_PROPERTY_EXTRACTOR_VERSION = "flow-load-properties/v1"
UNOBSERVED_PROPERTIES = ("schedule", "federation_state", "replication_state", "execution_mode")
FLOW_KINDS_WITH_PROPERTIES = (REPLICATION_FLOW, DATA_FLOW, TRANSFORMATION_FLOW)


def _tf_matches(target: str, member: str) -> bool:
    return target == member or target == f"{member}{DELTA_SHADOW_SUFFIX}"


def processing_property_facts(
    universe: AssessmentUniverse,
    entities: Mapping[str, Any],
    am: str,
    *,
    identity: Mapping[str, IdentityClassification] | None = None,
    evidence: Any | None = None,
) -> tuple[Fact, ...]:
    if evidence is None or not hasattr(evidence, "flow_properties"):
        return ()
    closure = universe.closure_of(am)
    ident = identity or {n: classify(entities[n]) for n in closure}
    members = [n for n in closure if not getattr(entities[n], "remote_entity", "") and not (n in ident and ident[n].identity.value == "SYSTEM")]
    produced: dict[tuple[str, str], set[str]] = {}
    for n in members:
        targets, _amb = evidence.target_flows(n, entities.keys())
        for fl, _m in targets:
            produced.setdefault((fl.kind, fl.name), set()).add(n)
        for fp in evidence.flow_properties:
            if fp.kind == TRANSFORMATION_FLOW and any(_tf_matches(t, n) for t in fp.targets):
                produced.setdefault((fp.kind, fp.name), set()).add(n)
    flows = []
    for fp in evidence.flow_properties:
        key = (fp.kind, fp.name)
        if key not in produced:
            continue
        rec = {"flow_kind": fp.kind, "flow_name": fp.name, "closure_targets": sorted(produced[key]), "sources": list(fp.sources), "targets": list(fp.targets),
               "properties": fp.properties}
        if fp.kind == DATA_FLOW:
            filters = []
            for s in fp.sources:
                d = evidence.derivation_evidence(s)
                if d is not None and (d.outer_where is not None or d.outer_having is not None):
                    filters.append({"source_entity": s, "where": d.outer_where, "having": d.outer_having})
            rec["source_filters"] = filters
            nodes = fp.properties.get("nodes") or []
            rec["observed"] = {"target_write_modes": sorted({n["mode"] for n in nodes if n["role"] == "target" and n["mode"]}),
                               "target_upsert": sorted({n["upsert"] for n in nodes if n["role"] == "target" and n["upsert"] is not None}),
                               "source_read_modes": sorted({n["mode"] for n in nodes if n["role"] == "source" and n["mode"]}),
                               "cds_subscriptions": sorted({(n.get("subscription_type") or "", n.get("subscription_name") or "") for n in nodes if n.get("subscription_type") or n.get("subscription_name")})}
            rec["observed"]["cds_subscriptions"] = [{"type": a or None, "name": b or None} for a, b in rec["observed"]["cds_subscriptions"]]
        elif fp.kind == REPLICATION_FLOW:
            tasks = fp.properties.get("tasks") or []
            rec["observed"] = {"load_types": sorted({t["load_type"] for t in tasks if t["load_type"]}), "truncate": sorted({t["truncate"] for t in tasks if t["truncate"] is not None}),
                               "delta_load_trigger": fp.properties.get("delta_load_trigger"), "delta_check_intervals": sorted({str(t["delta_check_interval"]) for t in tasks if t["delta_check_interval"] is not None}),
                               "delta_disabled": sorted({t["delta_disabled"] for t in tasks if t["delta_disabled"] is not None}),
                               "tasks_with_filters": sum(1 for t in tasks if t["filters"]), "tasks_with_change_capture_properties": sum(1 for t in tasks if t["change_capture_properties"])}
        else:
            rec["observed"] = {"load_type": fp.properties.get("load_type"), "delta_enabled": fp.properties.get("delta_enabled"), "truncate": fp.properties.get("truncate"),
                               "delta_annotations": fp.properties.get("delta_annotations")}
        flows.append(rec)
    value = {"extractor_version": FLOW_PROPERTY_EXTRACTOR_VERSION, "flows": sorted(flows, key=lambda r: (r["flow_kind"], r["flow_name"])),
             "counts": {"flows": len(flows), "by_kind": [{"kind": k, "count": sum(1 for f in flows if f["flow_kind"] == k)} for k in FLOW_KINDS_WITH_PROPERTIES]},
             "unobserved_properties": [{"property": p, "reason": "NOT_IN_EXPORT"} for p in UNOBSERVED_PROPERTIES], "inferences": [],
             "definition": "exact exported load properties of the flows that produce closure leaves; nothing is inferred (no FULL / DELTA / incremental / federated / replicated)"}
    refs = [EvidenceRef(f["flow_name"], "flow_properties", None, Plane.PROCESSING) for f in flows]
    return sort_facts([make_fact("ingestion.flow_load_properties", ING_, "closure", value, EvidenceClass.B, refs=refs, plane=Plane.PROCESSING)])
