"""Presentación de «Fuente / Extracción» e «Ingesta» (alternativa A): la evidencia de adquisición YA persistida se muestra; nada se recalcula.

Contrato probado: (1) Fuente = de dónde viene el dato (mecanismo, origen, contrato, evidencia); Ingesta = cómo entra y se carga (desglose por procedencia + propiedades
exportadas); (2) los indicadores de federación solo con artefactos remotos y evidencia evaluada; (3) «locales: origen no observable» solo cuando de verdad no hay
procedencia; (4) los controles sin hallazgos siguen accesibles (plegados, con su población) sin competir con los hallazgos; (5) no se afirma lo que la extracción no
evaluó; (6) la proyección no muta el perfil ni toca cobertura/confianza/Juicio; un perfil sin evidencia v3 se presenta como siempre.
"""
from __future__ import annotations

import copy
import inspect
import json
import re

from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import render_md
from webapp.main import templates
from webapp.services import assessment_acquisition as AQ
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc

TEMPLATE = "partials/catalog_assessment_report.html"
S4 = {"connection_id": "SRC_SYS", "connection_type": "SAPS4HANAOP", "container": "/CDS_EXTRACTION"}
ACQ, LOAD, BOUNDARY = "Adquisición de las hojas de frontera", "Cómo se carga", "Artefactos de frontera (hojas del cierre)"


def _leaf(name, kind, cls, *, systems=(), conn=None):
    return {"leaf": name, "leaf_kind": kind, "contract_class": cls, "classification_basis": "X", "classification_bases": [], "boundary_classes": [],
            "source_systems": list(systems), "remote_entities": [], "remote_connection": conn}


def _prov(name, state, basis, flows=(), impl="LOCAL_TABLE", conclusive=False):
    return {"leaf": name, "assessability": "ASSESSED", "implementation": impl, "identity": "UNKNOWN", "provenance_state": state, "basis": basis,
            "producer_flows": list(flows), "origin": None, "chain_complete": True, "scope": {"declared": conclusive, "conclusive": conclusive}}


def _flow(name, targets, loads, *, trigger="AT_SCHEDULED_TIME", interval=("3600",), disabled=(), cdc=0, filters=0, kind="REPLICATION_FLOW"):
    return {"flow_kind": kind, "flow_name": name, "closure_targets": list(targets), "sources": [], "targets": [],
            "properties": {"tasks": [{"name": "t", "filters": []}]},
            "observed": {"load_types": list(loads), "delta_load_trigger": trigger, "delta_check_intervals": list(interval), "delta_disabled": list(disabled),
                         "tasks_with_filters": filters, "tasks_with_change_capture_properties": cdc}}


def _area(aid, highlights, req=None):
    return {"id": aid, "macro_dimension": "SOURCE_EXTRACTION" if aid == "SOURCE_EXTRACTION" else "ARCHITECTURE_MODELING", "assessability": "ASSESSED", "reason": None,
            "required_surfaces": req or {"assessed": 2, "partially_assessed": 0, "not_assessed": 0, "not_applicable": 7}, "limitations_not_observable": [],
            "highlights": highlights, "finding_groups": []}


def _replication_doc(*, conclusive=False):
    """Modelo 100 % replicado: 3 hojas producidas por Replication Flow (2 REPLICATE con delta, 1 INITIAL), 0 artefactos remotos."""
    leaves = [_leaf(n, "LOCAL_FLOW_PRODUCED", "GOVERNED_SAP_CDS_CONTRACT", systems=[S4]) for n in ("T_A", "T_B", "T_C")]
    prov = [_prov("T_A", "ESTABLISHED", "REPLICATION_FLOW", [{"kind": "REPLICATION_FLOW", "name": "RF_A", "target_match": "DELTA_SHADOW_SUFFIX"}], conclusive=conclusive),
            _prov("T_B", "ESTABLISHED", "REPLICATION_FLOW", [{"kind": "REPLICATION_FLOW", "name": "RF_B", "target_match": "DELTA_SHADOW_SUFFIX"}], conclusive=conclusive),
            _prov("T_C", "ESTABLISHED", "REPLICATION_FLOW", [{"kind": "REPLICATION_FLOW", "name": "RF_C", "target_match": "EXACT"}], impl="STRUCTURE_ONLY", conclusive=conclusive)]
    flows = [_flow("RF_A", ["T_A"], ["REPLICATE"], cdc=1), _flow("RF_B", ["T_B"], ["REPLICATE"], cdc=1),
             _flow("RF_C", ["T_C"], ["INITIAL"], trigger="ON_DELTA_INTERVAL", interval=(), disabled=(True,))]
    return {
        "areas": [_area("SOURCE_EXTRACTION", [{"id": "remote_artifacts", "label": "Artefactos con origen remoto", "value": 0},
                                              {"id": "by_source_state", "label": "Por estado de la fuente", "value": []},
                                              {"id": "connections", "label": "Conexiones remotas distintas", "value": 0},
                                              {"id": "sources_with_filter", "label": "Fuentes remotas con filtro", "value": 0}]),
                  _area("INGESTION", [{"id": "boundary_artifacts", "label": BOUNDARY, "value": 3,
                                       "breakdown": [{"name": "con origen remoto observado", "count": 0}, {"name": "locales: origen no observable", "count": 3},
                                                     {"name": "provistos por la plataforma (sistema)", "count": 0}]},
                                      {"id": "duplicated_remote_entities_in_closure", "label": "Entidades remotas importadas más de una vez dentro del cierre", "value": 0}],
                        {"assessed": 8, "partially_assessed": 0, "not_assessed": 0, "not_applicable": 0})],
        "rule_results": [{"rule": "DIRECT_RAW_TABLE_ACCESS", "title": "Acceso directo a tabla física", "area": "SOURCE_EXTRACTION", "result": "ASSESSED_ABSENCE",
                          "assessability": "ASSESSED", "finding_count": 0, "reason": None},
                         {"rule": "DUPLICATED_REMOTE_IMPORT", "title": "Entidad remota importada más de una vez", "area": "INGESTION", "result": "ASSESSED_ABSENCE",
                          "assessability": "ASSESSED", "finding_count": 0, "reason": None}],
        "surfaces": [{"surface_id": "rule.direct_raw_table_access", "unit_counts": {"assessed": 3, "not_applicable": 0}, "assessability": "ASSESSED"},
                     {"surface_id": "rule.duplicated_remote_import", "unit_counts": {"assessed": 3, "not_applicable": 0}, "assessability": "ASSESSED"},
                     {"surface_id": "source.query_filter", "assessability": "NOT_APPLICABLE"}, {"surface_id": "source.connection_consistency", "assessability": "NOT_APPLICABLE"}],
        "canonical_evidence": {"version": "canonical-evidence/v3", "source_contract_classes": {"leaves": leaves}, "leaf_provenance": prov,
                               "processing_flow_properties": {"flows": flows, "unobserved_properties": [{"property": "schedule", "reason": "NOT_IN_EXPORT"},
                                                                                                       {"property": "execution_mode", "reason": "NOT_IN_EXPORT"}]}}}


def _federated_doc(*, filter_state="ASSESSED"):
    """Modelo con federación, un dataset declarado y una hoja sin procedencia evaluable (evidencia parcial)."""
    leaves = [_leaf("R_1", "REMOTE_IMPORTED", "GOVERNED_SAP_CDS_CONTRACT", conn="CONN_A"), _leaf("R_2", "REMOTE_IMPORTED", "PHYSICAL_ABAP_TABLE_BOUNDARY", conn="CONN_A"),
              _leaf("D_1", "LOCAL_DECLARED_SOURCE", "EXTERNAL_CONTRACT_UNDETERMINED"), _leaf("U_1", "LOCAL_PROVENANCE_NOT_ASSESSED", "UNKNOWN"),
              _leaf("P_1", "PLATFORM_OBJECT", "PLATFORM_PROVIDED")]
    prov = [_prov("D_1", "DECLARED", "DECLARED_DATASET"), _prov("U_1", None, "NONE")]
    doc = _replication_doc()
    doc["areas"][0]["highlights"] = [{"id": "remote_artifacts", "label": "Artefactos con origen remoto", "value": 2},
                                     {"id": "by_source_state", "label": "Por estado de la fuente", "value": [{"name": "CDS_VIEW", "count": 1}, {"name": "RAW_PHYSICAL_TABLE", "count": 1}]},
                                     {"id": "connections", "label": "Conexiones remotas distintas", "value": 1},
                                     {"id": "sources_with_filter", "label": "Fuentes remotas con filtro", "value": 0}]
    doc["areas"][1]["highlights"][0] = {"id": "boundary_artifacts", "label": BOUNDARY, "value": 5,
                                        "breakdown": [{"name": "con origen remoto observado", "count": 2}, {"name": "locales: origen no observable", "count": 2},
                                                      {"name": "provistos por la plataforma (sistema)", "count": 1}]}
    doc["surfaces"] = [{"surface_id": "source.query_filter", "assessability": filter_state}, {"surface_id": "source.connection_consistency", "assessability": "ASSESSED"}]
    doc["canonical_evidence"] = {"version": "canonical-evidence/v3", "source_contract_classes": {"leaves": leaves}, "leaf_provenance": prov,
                                 "processing_flow_properties": {"flows": [], "unobserved_properties": []}}
    return doc


def _view_of(doc, aid):
    rules = {}
    for r in doc["rule_results"]:
        rules.setdefault(r["area"], []).append(r)
    a = next(x for x in doc["areas"] if x["id"] == aid)
    return presenter._area_view(a, {}, rules, doc)


def _rows(view, title):
    for g in view["facts"]["groups"]:
        for t in g["tables"]:
            if t["title"] == title:
                return t["rows"]
    return None


# ── Fuente / Extracción ────────────────────────────────────────────────────────────────
def test_source_shows_mechanism_origin_contract_and_evidence_of_a_replication_only_model():
    v = _view_of(_replication_doc(), "SOURCE_EXTRACTION")
    assert _rows(v, ACQ) == [["Replication Flow", "SRC_SYS (SAPS4HANAOP) · /CDS_EXTRACTION", "CDS gobernado SAP", 3, "Observada"]]
    assert v["facts"]["groups"][0]["title"] == ACQ                                                       # primera evidencia del colapsable
    assert v["facts"]["headline"] == []                                                                   # sin artefactos remotos: ni 0/0/0 ni «Por estado de la fuente» vacío
    assert not any(g["title"] == "Por estado de la fuente" for g in v["facts"]["groups"])
    assert v["facts"]["empty_state"] == {"label": "Sin origen remoto", "text": "El alcance evaluado no contiene artefactos con origen remoto (tablas importadas desde otra conexión); "
                                                                            "las superficies de contrato remoto no aplican a este modelo."}
    assert not any("no contiene artefactos" in n for n in v["facts"]["notes"])                            # ya no es texto corrido
    assert _view_of(_federated_doc(), "SOURCE_EXTRACTION")["facts"]["empty_state"] is None                 # con origen remoto real: comportamiento actual
    assert "7 no aplican" in v["surfaces"]                                                                # la cobertura de superficies se conserva tal cual


def test_federation_indicators_appear_only_with_remote_artifacts_and_evaluated_evidence():
    v = _view_of(_federated_doc(), "SOURCE_EXTRACTION")
    assert [k["label"] for k in v["facts"]["headline"]] == ["Artefactos con origen remoto", "Conexiones remotas distintas", "Fuentes remotas con filtro"]
    assert _rows(v, "Nombre") is None and any(g["title"] == "Por estado de la fuente" for g in v["facts"]["groups"])
    partial = _view_of(_federated_doc(filter_state="PARTIALLY_ASSESSED"), "SOURCE_EXTRACTION")
    labels = [k["label"] for k in partial["facts"]["headline"]]
    assert "Fuentes remotas con filtro" not in labels and "Artefactos con origen remoto" in labels      # un 0 de una superficie no evaluada no es un dato


def test_federated_and_unknown_sources_keep_their_provenance_states_distinct():
    rows = _rows(_view_of(_federated_doc(), "SOURCE_EXTRACTION"), ACQ)
    by_mech = {r[0]: r for r in rows}
    assert by_mech["Dataset externo declarado"][4] == "Declarada" and by_mech["Sin productor identificado"][4] == "No evaluable"
    assert by_mech["Objeto de la plataforma"][4] == "Provista por la plataforma" and by_mech["Tabla remota importada"][4] == "Observada"
    assert sum(r[3] for r in rows) == 5                                                                   # cada hoja cuenta una sola vez


# ── Ingesta ────────────────────────────────────────────────────────────────────────────
def test_ingestion_relabels_origin_not_observable_when_a_producer_resolves_it_and_the_partition_stays_exhaustive():
    rows = _rows(_view_of(_replication_doc(), "INGESTION"), BOUNDARY)
    assert rows == [["Total", 3], ["locales con productor identificado (flujo)", 3]]
    assert "locales: origen no observable" not in str(rows)
    fed = _rows(_view_of(_federated_doc(), "INGESTION"), BOUNDARY)
    d = dict(map(tuple, fed))
    assert d["Total"] == 5 and sum(n for k, n in fed if k != "Total") == 5                                # partición exhaustiva y excluyente
    assert d["locales con origen declarado"] == 1 and d["locales: origen sin evaluar"] == 1 and "locales: origen no observable" not in d


def test_ingestion_shows_only_exported_load_properties_and_never_infers_full_or_delta():
    v = _view_of(_replication_doc(), "INGESTION")
    rows = _rows(v, LOAD)
    assert rows[0] == ["Replication Flow · REPLICATE", 2, "Tabla local con captura de delta",
                       "disparador del delta AT_SCHEDULED_TIME; intervalo de verificación 3600 s; captura de cambios declarada; sin filtros declarados"]
    assert rows[1][0] == "Replication Flow · INITIAL" and "delta deshabilitado" in rows[1][3] and rows[1][2].startswith("Estructura local")
    text = str(rows) + " ".join(v["facts"]["notes"])
    assert not re.search(r"\bFULL\b|incremental|federad[oa]|replicad[oa]", text, re.I)                   # lo no exportado no se infiere
    assert any("calendario de ejecución" in n and "modo de ejecución" in n for n in v["facts"]["notes"])


def test_the_absence_of_other_flow_families_is_never_asserted_unless_the_scope_is_conclusive():
    notes = " ".join(_view_of(_replication_doc(), "INGESTION")["facts"]["notes"])
    assert "No se evaluó la presencia de Data Flows, Transformation Flows ni Task Chains" in notes
    assert "No se evaluó" not in " ".join(_view_of(_replication_doc(conclusive=True), "INGESTION")["facts"]["notes"])
    assert "ausencia de Data Flows" not in notes and "sin Data Flows" not in notes


def test_the_remote_duplicates_tile_is_hidden_without_remote_artifacts_only():
    assert [k["label"] for k in _view_of(_replication_doc(), "INGESTION")["facts"]["headline"]] == [BOUNDARY]
    assert len(_view_of(_federated_doc(), "INGESTION")["facts"]["headline"]) == 2


# ── Reglas: hallazgos vs trazabilidad ──────────────────────────────────────────────────
def test_rules_without_findings_are_folded_with_their_population_but_findings_and_unestablished_absence_stay_in_the_table():
    doc = _replication_doc()
    doc["rule_results"] += [{"rule": "LAYER_BOUNDARY_REVIEW", "title": "Fuente remota consumida en capa 3", "area": "INGESTION", "result": "ABSENCE_NOT_ESTABLISHED",
                             "assessability": "NOT_ASSESSED", "finding_count": 0, "reason": None},
                            {"rule": "ROLE_LAYER_DIVERGENCE", "title": "Capa por nombre", "area": "INGESTION", "result": "PATTERN_FOUND", "assessability": "ASSESSED",
                             "finding_count": 2, "reason": None}]
    doc["surfaces"].append({"surface_id": "rule.role_layer_divergence", "unit_counts": {"assessed": 9, "not_applicable": 0}})
    v = _view_of(doc, "INGESTION")
    assert v["rules_split"] and [r["title"] for r in v["rules_open"]] == ["Fuente remota consumida en capa 3", "Capa por nombre"]
    assert [(r["title"], r["population"]) for r in v["rules_clean"]] == [("Entidad remota importada más de una vez", "3 de 3 hojas de frontera evaluadas")]
    src = _view_of(doc, "SOURCE_EXTRACTION")
    assert src["rules_open"] == [] and src["rules_clean"][0]["population"] == "3 de 3 hojas de frontera evaluadas"


def test_a_not_applicable_control_never_claims_an_evaluated_population():
    doc = _replication_doc()
    doc["rule_results"].append({"rule": "ROLE_LAYER_DIVERGENCE", "title": "Capa por nombre", "area": "INGESTION", "result": "NOT_APPLICABLE", "assessability": "NOT_APPLICABLE",
                                "finding_count": 0, "reason": None})
    doc["surfaces"].append({"surface_id": "rule.role_layer_divergence", "unit_counts": {"assessed": 0, "not_applicable": 78}})
    clean = {r["title"]: r["population"] for r in _view_of(doc, "INGESTION")["rules_clean"]}
    assert clean["Capa por nombre"] == "—"


def test_other_areas_keep_their_rule_table_untouched():
    doc = _replication_doc()
    doc["areas"].append(_area("PROPAGATION", []))
    doc["rule_results"].append({"rule": "PASS_THROUGH_REVIEW", "title": "Capa de paso", "area": "PROPAGATION", "result": "ASSESSED_ABSENCE", "assessability": "ASSESSED",
                                "finding_count": 0, "reason": None})
    v = _view_of(doc, "PROPAGATION")
    assert not v["rules_split"] and [r["title"] for r in v["rules"]] == ["Capa de paso"] and v["rules_clean"] == []


# ── perfiles sin evidencia v3 y no-mutación ────────────────────────────────────────────
def test_a_profile_without_canonical_evidence_is_presented_exactly_as_before():
    doc = _replication_doc()
    del doc["canonical_evidence"]
    s, i = _view_of(doc, "SOURCE_EXTRACTION"), _view_of(doc, "INGESTION")
    assert [k["label"] for k in s["facts"]["headline"]] == ["Artefactos con origen remoto", "Conexiones remotas distintas", "Fuentes remotas con filtro"]
    assert _rows(i, BOUNDARY)[2] == ["locales: origen no observable", 3] and _rows(i, LOAD) is None
    assert AQ.source_facts(doc) is None and AQ.load_facts(doc) is None and AQ.acquisition_rows(doc) is None


def test_the_projection_never_mutates_the_profile():
    doc = _replication_doc()
    before = copy.deepcopy(doc)
    _view_of(doc, "SOURCE_EXTRACTION"), _view_of(doc, "INGESTION")
    assert doc == before


def test_the_module_only_projects_persisted_evidence_and_does_not_reach_the_judgment_or_the_engine():
    src = inspect.getsource(AQ)
    assert not re.search(r"^\s*(from|import)\s+.*(methodological_judgment|assessability|signals|shadow|profile)\b", src, re.M)


# ── integración con un perfil real del motor (pool sintético) ───────────────────────────
def test_engine_profile_renders_both_collapsibles_in_order_without_changing_coverage_confidence_or_the_profile(tmp_path):
    from tests._modeling_assessment_m7_fixtures import write_inbound
    from tools.catalog.modeling_assessment import shadow
    doc, _meta = shadow.assess(write_inbound(tmp_path / "pool"), "Acme", "4AM_ACME")
    before = copy.deepcopy(doc)
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_ACME"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_ACME", generated_at="2026-10-04T00:00:00+00:00", profile=doc)
    view = presenter.report_view(status, entry, "datasphere_core")
    html = templates.get_template(TEMPLATE).render(v=view)
    assert doc == before                                                                                  # perfil (cobertura, confianza, Juicio, evidencia) intacto
    order = [m.group(1) for m in re.finditer(r'id="asr-a-([A-Z_]+)"', html)]
    assert order.index("INGESTION") < order.index("SOURCE_EXTRACTION")                                    # dos colapsables conservados, con su orden
    assert "Superficies de evidencia requeridas" in html and "Evaluación de reglas" in html               # las demás áreas conservan su tabla
    assert view["summary"]["coverage_strict"] and view["summary"]["confidence"]


# ── federados: sin repetir la clasificación de contratos remotos ────────────────────────
def test_remote_rows_point_to_the_state_table_instead_of_repeating_the_contract_classification():
    rows = _rows(_view_of(_federated_doc(), "SOURCE_EXTRACTION"), ACQ)
    remote = [r for r in rows if r[0] == "Tabla remota importada"]
    assert remote == [["Tabla remota importada", "CONN_A", "Ver «Por estado de la fuente»", 2, "Observada"]]      # una fila por conexión; sin CDS/tabla física repetidos
    assert not any(c in str(remote) for c in ("CDS gobernado", "Tabla física ABAP", "ODP/SAPI"))
    others = {r[0]: r for r in rows if r[0] != "Tabla remota importada"}
    assert others["Dataset externo declarado"][2] == "Contrato externo no determinado" and others["Dataset externo declarado"][4] == "Declarada"
    assert others["Sin productor identificado"][4] == "No evaluable" and others["Objeto de la plataforma"][2] == "Provisto por la plataforma"
    assert sum(r[3] for r in rows) == 5                                                                       # mismas columnas, misma cobertura de hojas


def test_replication_flow_rows_keep_their_governed_contract_because_it_is_different_evidence():
    doc = _federated_doc()
    doc["canonical_evidence"]["source_contract_classes"]["leaves"].append(_leaf("F_1", "LOCAL_FLOW_PRODUCED", "GOVERNED_SAP_CDS_CONTRACT", systems=[S4]))
    doc["canonical_evidence"]["leaf_provenance"].append(_prov("F_1", "ESTABLISHED", "REPLICATION_FLOW", [{"kind": "REPLICATION_FLOW", "name": "RF_1", "target_match": "EXACT"}]))
    rows = _rows(_view_of(doc, "SOURCE_EXTRACTION"), ACQ)
    assert ["Replication Flow", "SRC_SYS (SAPS4HANAOP) · /CDS_EXTRACTION", "CDS gobernado SAP", 1, "Observada"] in rows


def test_without_the_state_table_the_remote_rows_keep_their_contract_class():
    doc = _federated_doc()
    doc["areas"][0]["highlights"] = [h for h in doc["areas"][0]["highlights"] if h["id"] != "by_source_state"]
    rows = _rows(_view_of(doc, "SOURCE_EXTRACTION"), ACQ)
    assert any(r[0] == "Tabla remota importada" and r[2] == "CDS gobernado SAP" for r in rows)


# ── paridad HTML / Markdown / JSON ────────────────────────────────────────────────────
def _md_breakdown(doc):
    area = next(a for a in doc["areas"] if a["id"] == "INGESTION")
    lines = render_md._area_block(area, {}, {}, "###", doc)
    out, on = [], False
    for ln in lines:
        if ln.startswith("- Artefactos de frontera"):
            on = True
        elif on and ln.startswith("  - "):
            name, _, n = ln[4:].rpartition(": ")
            out.append((name, int(n)))
        elif on:
            break
    return out


def _json_derived(doc):
    """Lo que un consumidor del JSON canónico deriva de ``canonical_evidence`` (sin tocar el rótulo histórico)."""
    kinds = {}
    for leaf in doc["canonical_evidence"]["source_contract_classes"]["leaves"]:
        kinds[leaf["leaf_kind"]] = kinds.get(leaf["leaf_kind"], 0) + 1
    return {"con origen remoto observado": kinds.get("REMOTE_IMPORTED", 0), "locales con productor identificado (flujo)": kinds.get("LOCAL_FLOW_PRODUCED", 0),
            "locales con origen declarado": kinds.get("LOCAL_DECLARED_SOURCE", 0), "locales: origen sin evaluar": kinds.get("LOCAL_PROVENANCE_NOT_ASSESSED", 0),
            "provistos por la plataforma (sistema)": kinds.get("PLATFORM_OBJECT", 0)}


def test_html_markdown_and_json_read_the_same_provenance_for_the_boundary_leaves():
    for doc in (_replication_doc(), _federated_doc()):
        html_rows = [tuple(r) for r in _rows(_view_of(doc, "INGESTION"), BOUNDARY) if r[0] != "Total"]
        md_rows = _md_breakdown(doc)
        assert html_rows == [(k, v) for k, v in md_rows]                                                     # HTML y Markdown: mismo reparto, mismos rótulos
        derived = {k: v for k, v in _json_derived(doc).items() if v}
        assert dict(md_rows) == derived                                                                       # y es el que se deriva de la evidencia del JSON
        assert "locales: origen no observable" not in dict(md_rows)


def test_markdown_for_raw_gl_like_model_shows_the_replication_producers_not_origin_not_observable():
    md = render_md._area_block(next(a for a in _replication_doc()["areas"] if a["id"] == "INGESTION"), {}, {}, "###", _replication_doc())
    text = chr(10).join(md)
    assert "locales con productor identificado (flujo): 3" in text and "origen no observable" not in text


def test_json_export_keeps_the_historical_label_and_is_byte_stable_while_markdown_and_html_project_the_provenance():
    doc = _replication_doc()
    before = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    _view_of(doc, "INGESTION")
    render_md._area_block(next(a for a in doc["areas"] if a["id"] == "INGESTION"), {}, {}, "###", doc)
    assert json.dumps(doc, sort_keys=True, ensure_ascii=False) == before                                      # el documento canónico (y su firma) no se reescribe
    assert next(h for h in doc["areas"][1]["highlights"] if h["id"] == "boundary_artifacts")["breakdown"][1] == {"name": "locales: origen no observable", "count": 3}


def test_markdown_without_canonical_evidence_is_exactly_the_historical_one():
    doc = _replication_doc()
    del doc["canonical_evidence"]
    assert _md_breakdown(doc)[1] == ("locales: origen no observable", 3)
    assert ma_profile.canonical_assessment_json.__name__                                                       # (el export JSON sigue siendo el mismo serializador canónico)


def test_the_no_remote_message_renders_with_the_hub_empty_state_pattern_only_in_source_extraction(tmp_path):
    from tests._modeling_assessment_m7_fixtures import write_inbound
    from tools.catalog.modeling_assessment import shadow
    doc, _meta = shadow.assess(write_inbound(tmp_path / "pool"), "Acme", "4AM_ACME")
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_ACME"]})()
    view = presenter.report_view(svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_ACME", generated_at="2026-10-04T00:00:00+00:00", profile=doc), entry, "datasphere_core")
    baseline = templates.get_template(TEMPLATE).render(v=view)
    assert "Sin origen remoto" not in baseline                                                            # con origen remoto real (o sin evidencia): HTML idéntico al actual
    src = next(a for a in view["areas"] if a["id"] == "SOURCE_EXTRACTION")
    src["facts"]["empty_state"] = _view_of(_replication_doc(), "SOURCE_EXTRACTION")["facts"]["empty_state"]
    html = templates.get_template(TEMPLATE).render(v=view)
    block = re.search(r'<div class="dashboard-empty"><span class="dashboard-empty__label">Sin origen remoto</span><p>(.*?)</p></div>', html, re.S)
    assert block and html.count("Sin origen remoto") == 1                                                  # primitivo existente `.dashboard-empty`, label + pie
    area = html[html.index('id="asr-a-SOURCE_EXTRACTION"'):]
    assert area.index("Superficies de evidencia requeridas") < area.index("Sin origen remoto")             # debajo de la línea de superficies
    assert "no contiene artefactos con origen remoto" in block.group(1) and "no aplican a este modelo" in block.group(1)
    assert baseline != html
    assert html.replace(block.group(0), "").count("dashboard-empty") == baseline.count("dashboard-empty")    # nada más cambia
