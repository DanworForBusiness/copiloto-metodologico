"""Constructor de perfiles SINTETICOS minimos (forma del perfil publico persistido) para probar el motor de Juicio sin pool ni datos de clientes."""
from __future__ import annotations

from typing import Any


class PB:
    """Perfil minimo: solo las secciones que lee ``ProfileEvidence``. Todo es explicito; no hay nombres de clientes."""

    def __init__(self, am: str = "AM"):
        self.am = am
        self._views: dict[str, dict] = {}
        self._cands: dict[str, dict] = {}
        self._remote: list[str] = []
        self._raw: dict[str, bool] = {}
        self._local: dict[str, dict] = {}
        self._boundaries: dict[str, str] = {}
        self._measures: list[dict] = []
        self._fields: list[dict] = []
        self._assoc: list[tuple[str, str]] = []
        self._declared_not_assoc: list[str] = []
        self._product: list[str] = []
        self._other_origin = 0
        self._reuse: list[dict] = []
        self._occ: list[dict] = []
        self._reconv = 0
        self._topology: dict[str, dict] = {}
        self._areas = {a: "ASSESSED" for a in ("ANALYTIC_MODEL", "PROPAGATION", "HARMONIZATION", "INGESTION", "NAMING", "SOURCE_EXTRACTION")}
        self._naming = (10, 0)
        self._flows: list[dict] = []
        self._derived = 0
        self._state = "ASSESSED"
        self._closure_extra: list[str] = []

    # ───────── construccion ─────────
    def view(self, name, providers=(), *, joins=0, derived=0, where=0, unions=0, nodes=1, depth=1, vtype="SQL", basis="DECLARED_ROLE", dist=None):
        self._views[name] = {"artifact": name, "view_type": vtype, "view_type_basis": "DERIVED", "inferred_layer": "3", "inferred_layer_basis": basis, "providers": len(providers),
                             "provider_names": list(providers), "providers_in_closure": len(providers), "dependents_in_closure": 1,
                             "metrics": {"select_nodes": nodes, "graph_depth": depth, "joins": joins, "unions": unions, "derived_columns": derived, "output_columns": 3,
                                         "filters": {"where_nodes": where, "having_nodes": 0}, "function_calls": 0}}
        if dist is not None:
            self._topology[name] = {"artifact": name, "lineage_distance_to_root": dist, "direct_providers": len(providers), "boundary_providers": 0}
        return self

    def cand(self, name, *, character="TECHNICAL_MATERIALIZATION", dimension=False, carry=0, sources=(), label_diff=0, mapping_determinable=True):
        self._cands[name] = {"artifact": name, "implementation": "QUERY_DEFINED", "mapping_determinable": mapping_determinable, "sources": list(sources),
                             "character": {"value": character}, "declared_role": {"declares_dimension": dimension}, "source_vocabulary": {"fields_with_source_column_name": carry},
                             "label_origin_counts": {"DIFFERENT_FROM_PROVIDER": label_diff, "EQUALS_PROVIDER_LABEL": 1}}
        return self

    def remote(self, name, *, raw=False, spine=True):
        self._remote.append(name)
        if raw:
            self._raw[name] = spine
        return self

    def local_leaf(self, name, *, state="ESTABLISHED", boundary="GOVERNED_CDS_CONTRACT"):
        self._local[name] = {"leaf": name, "provenance_state": state, "assessability": "ASSESSED" if state else "NOT_ASSESSED"}
        self._boundaries[name] = boundary
        return self

    def measure(self, name, *, enriched=True):
        self._measures.append({"technical_name": name, "role": "MEASURE", "business_name_equals_technical": not enriched, "name_equals_remote_source_column": False, "derived": False})
        return self

    def field(self, name, *, technical=False):
        self._fields.append({"technical_name": name, "role": "ATTRIBUTE", "business_name_equals_technical": technical, "name_equals_remote_source_column": technical, "derived": False})
        return self

    def assoc(self, target, category="BUSINESS_DIMENSION"):
        self._assoc.append((target, category))
        return self

    def product(self, *nodes):
        self._product = list(nodes)
        return self

    def other_origin(self, n=1):
        self._other_origin = n
        return self

    def temporal_reuse(self, *artifacts, clause="SELECT"):
        self._reuse.append({"exact_fp": "fp", "transformation_classes": ["TEMPORAL"], "occurrences": [{"artifact": a, "clause": clause, "containment": "WHOLE"} for a in artifacts]})
        return self

    def reconvergence(self, n=1):
        self._reconv = n
        return self

    def naming(self, applicable, non_compliant):
        self._naming = (applicable, non_compliant)
        return self

    def area(self, name, state):
        self._areas[name] = state
        return self

    def flow(self, name="RF", delta=True):
        self._flows.append({"flow_kind": "REPLICATION_FLOW", "flow_name": name, "observed": {}, "properties": {"delta_load_trigger": "AT_SCHEDULED_TIME" if delta else None, "tasks": []}})
        return self

    # ───────── salida ─────────
    def build(self) -> dict[str, Any]:
        names = {self.am, *self._views, *self._cands, *self._remote, *self._local}
        for v in self._views.values():
            names.update(v["provider_names"])
        views = list(self._views.values())
        fields = [*self._fields, *self._measures]
        occ_groups = self._reuse
        raw_values = [{"occurrence_id": f"DIRECT_RAW_TABLE_ACCESS-{i:04d}", "affected_artifacts": [n], "evidence_facts": [f"source.downstream_reach@{n}"],
                       "values": {"reaches_terminal_by_strict_lineage": spine}} for i, (n, spine) in enumerate(sorted(self._raw.items()))]
        groups = [{"id": "DIRECT_RAW_TABLE_ACCESS", "occurrences": raw_values}] if raw_values else []
        fi = [{"fact": f"source.contract@{n}"} for n in self._remote] + [{"fact": f"source.downstream_reach@{n}"} for n in self._remote]
        areas = [{"id": a, "assessability": st, "highlights": []} for a, st in self._areas.items()]
        for a in areas:
            if a["id"] == "ANALYTIC_MODEL":
                a["highlights"].append({"id": "direct_lineage_providers", "value": list(self._product)})
            if a["id"] == "SOURCE_EXTRACTION":
                counts = []
                n_raw = len(self._raw)
                n_gov = len(self._remote) - n_raw - self._other_origin
                if n_gov > 0:
                    counts.append({"name": "CDS_VIEW", "count": n_gov})
                if n_raw:
                    counts.append({"name": "RAW_PHYSICAL_TABLE", "count": n_raw})
                if self._other_origin:
                    counts.append({"name": "OTHER_ORIGIN", "count": self._other_origin})
                a["highlights"].append({"id": "by_source_state", "value": counts})
        assoc = [{"field": f"_{t}", "target": t, "category": c} for t, c in self._assoc]
        ce = {
            "closure": {"rules": "closure-rules/v1"},
            "view_complexity": {"views": views, "count": len(views)},
            "harmonization_contract": {"artifacts": list(self._cands.values())},
            "published_contract": {"fields": {"fields": fields}, "associations": {"associations": assoc, "declared_dimensions_in_closure_not_associated": list(self._declared_not_assoc)}},
            "leaf_provenance": list(self._local.values()),
            "physical_source_boundaries": {"leaves": [{"leaf": n, "boundaries": [{"boundary_class": c}], "chain_complete": True} for n, c in self._boundaries.items()]},
            "expression_occurrences": {"occurrences": self._occ, "reuse_groups": occ_groups, "trivial_predicates": []},
            "branch_reconvergence": {"reconvergences": [{"shared_provider": "x"}] * self._reconv, "counts": {"pairs": self._reconv}},
            "assembly_topology": {"artifacts": list(self._topology.values())},
            "naming_evidence": {"hygiene": {"artifact_name_populations": {"section_28": {"applicable": self._naming[0], "non_compliant": self._naming[1]}}}},
            "processing_flow_properties": {"flows": self._flows},
            "derivation_semantics": {"counts": {"derived_fields": self._derived}},
        }
        return {"schema_version": "x", "assessment_state": self._state, "versions": {"canonical_evidence": "canonical-evidence/v2"},
                "scope": {"terminal_analytic_model": self.am, "closure_artifacts": sorted(names), "pool_signature": "sig"}, "summary": {"context_required": {"questions": []}},
                "areas": areas, "observations": [], "review_required": groups, "surfaces": [], "fact_index": fi, "canonical_evidence": ce}
