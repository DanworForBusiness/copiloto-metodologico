"""test_modeling_assessment_view_type.py — hecho `am.view_type_composition` (View Type Intelligence, Gate 2).

Contrato:
  * `viewType` (GRAPHICAL | SQL | UNKNOWN) se CONSUME del JSON exportado (`STORED`) o se DERIVA con la MISMA función del exportador
    (`DERIVED`; los exports anteriores al campo no lo traen). Fuente única: `tools/catalog/dsp_view_type.py`;
  * qué es una vista: la familia `views` cuando el pool la informa; en pools PLANOS solo la evidencia estructural del exportador
    (marcador GRAPHICAL o SQL), excluyendo AM / tabla local / tabla remota; nunca por nombre de archivo;
  * el conteo es de vistas ÚNICAS del cierre semántico y cumple graphical + sql + unknown == total; sin vistas, porcentajes None;
  * es evidencia DESCRIPTIVA: sin señales, sin penalidades, sin cambios de cobertura/versiones/veredicto legacy; los perfiles anteriores siguen legibles.
Fixtures SINTÉTICAS (nombres genéricos).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._modeling_assessment_m7_fixtures import _view, _write
from tests.test_modeling_assessment_graph import ent, pool
from tools.catalog import dsp_view_type as vt
from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment import render_md, shadow
from tools.catalog.modeling_assessment.facts_view_type import view_membership, view_type_facts
from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
from tools.catalog.modeling_assessment.scope import AssessmentUniverse

SQL_MARK = {"@DataWarehouse.sqlEditor.query": "SELECT 1 FROM X"}


def _body(name, extra=None, *, graphical=False, stored=None, editor_other=False):
    d = {"definitions": {name: {"kind": "entity", **(extra or {})}}}
    if graphical:
        d["editorSettings"] = {name: {"editor": {"default": "GRAPHICALVIEWBUILDER"}}}
    if editor_other:
        d["editorSettings"] = {name: {"editor": {"default": "SQLEDITOR"}}}
    if stored is not None:
        d["viewType"] = stored
    return d


def _evidence(root: Path, files: dict[str, dict]) -> SupplementaryEvidence:
    paths = [_write(root, rel, body) for rel, body in files.items()]
    return SupplementaryEvidence.from_files(paths, root=root, view_type_of=bridge.view_type_of_export, family_of_dir=bridge.artifact_family_of_dir)


def _fact(entities, root_name, evidence):
    universe = AssessmentUniverse.from_entities(entities)
    (f,) = view_type_facts(universe, entities, root_name, evidence=evidence)
    return f.value


# ── 1-3. consumo/derivación del tipo, con procedencia explícita ─────────────
def test_the_exporter_classifier_is_the_single_source_and_precedence_is_exact():
    assert vt.classify_view_type(_body("A", graphical=True), "A") == "GRAPHICAL"
    assert vt.classify_view_type(_body("A", SQL_MARK), "A") == "SQL"
    assert vt.classify_view_type(_body("A"), "A") == "UNKNOWN"
    assert vt.classify_view_type(_body("A", SQL_MARK, graphical=True), "A") == "GRAPHICAL"        # ambos marcadores: GRAPHICAL gana (contrato del exportador)
    assert vt.classify_view_type(_body("A", SQL_MARK, editor_other=True), "A") == "SQL"
    assert vt.classify_view_type([], "A") == "UNKNOWN" and vt.classify_view_type({"definitions": []}, "A") == "UNKNOWN"
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    assert dsp.LineageExporter.classify_view_type(_body("A", SQL_MARK, graphical=True), "A") == "GRAPHICAL"      # el exportador delega: misma función
    assert bridge.classify_view_type is vt.classify_view_type and bridge.view_type_of_export is vt.view_type_of_export
    import inspect
    src = inspect.getsource(dsp.LineageExporter.classify_view_type)
    assert "GRAPHICALVIEWBUILDER" not in src and "sqlEditor" not in src                        # sin segunda implementación en el exportador


def test_stored_view_type_wins_and_unknown_values_fall_back_to_derivation():
    assert vt.view_type_of_export(_body("A", stored="SQL"), "A") == ("SQL", "STORED")           # el JSON ya traía el campo (aunque no haya marcador)
    assert vt.view_type_of_export(_body("A", SQL_MARK, stored="GRAPHICAL"), "A") == ("GRAPHICAL", "STORED")
    assert vt.view_type_of_export(_body("A", SQL_MARK, stored="WHATEVER"), "A") == ("SQL", "DERIVED")   # valor desconocido: se deriva
    assert vt.view_type_of_export(_body("A", SQL_MARK), "A") == ("SQL", "DERIVED")


def test_graphical_sql_and_unknown_are_consumed_with_their_basis(tmp_path):
    ents = pool(ent("AM_T", up=["VF_G", "VF_S", "VF_U", "VF_ST"], cube=True), ent("VF_G"), ent("VF_S"), ent("VF_U"), ent("VF_ST"))
    ev = _evidence(tmp_path, {
        "views/VF_G.json": _body("VF_G", graphical=True), "views/VF_S.json": _body("VF_S", SQL_MARK), "views/VF_U.json": _body("VF_U"),
        "views/VF_ST.json": _body("VF_ST", stored="SQL"), "analytic-models/AM_T.json": _body("AM_T", SQL_MARK)})
    v = _fact(ents, "AM_T", ev)
    assert (v["total_views"], v["graphical_views"], v["sql_views"], v["unknown_views"]) == (4, 1, 2, 1)       # UNKNOWN sigue explícito
    by = {r["technical_name"]: r for r in v["views"]}
    assert by["VF_G"]["view_type"] == "GRAPHICAL" and by["VF_G"]["view_type_basis"] == "DERIVED"
    assert by["VF_S"]["view_type"] == "SQL" and by["VF_S"]["view_type_basis"] == "DERIVED"
    assert by["VF_ST"]["view_type"] == "SQL" and by["VF_ST"]["view_type_basis"] == "STORED"
    assert by["VF_U"]["view_type"] == "UNKNOWN" and all(r["membership_basis"] == "FAMILY_DIRECTORY" for r in v["views"])
    assert (v["graphical_view_pct"], v["sql_view_pct"], v["unknown_view_pct"]) == (0.25, 0.5, 0.25)


# ── 4-5. deduplicación e invariante ─────────────────────────────────────────
def test_a_view_reachable_by_two_paths_counts_once_and_the_invariant_holds(tmp_path):
    ents = pool(ent("AM_D", up=["VF1", "VF2"], cube=True), ent("VF1", up=["V_SHARED", "V_A"]), ent("VF2", up=["V_SHARED", "V_B"]),
                ent("V_SHARED"), ent("V_A"), ent("V_B"))
    ev = _evidence(tmp_path, {"views/VF1.json": _body("VF1", SQL_MARK), "views/VF2.json": _body("VF2", graphical=True),
                              "views/V_SHARED.json": _body("V_SHARED", graphical=True), "views/V_A.json": _body("V_A"), "views/V_B.json": _body("V_B", SQL_MARK)})
    v = _fact(ents, "AM_D", ev)
    names = [r["technical_name"] for r in v["views"]]
    assert sorted(names) == ["VF1", "VF2", "V_A", "V_B", "V_SHARED"] and len(names) == len(set(names)) == v["total_views"] == 5   # V_SHARED, una vez
    assert v["graphical_views"] + v["sql_views"] + v["unknown_views"] == v["total_views"]
    assert names == sorted(names)                                                                # orden determinista


def test_without_views_percentages_are_none_and_the_invariant_holds(tmp_path):
    ents = pool(ent("AM_E", up=["LOCAL_T"], cube=True), ent("LOCAL_T", repository_type="DWC_LOCAL_TABLE"))
    v = _fact(ents, "AM_E", _evidence(tmp_path, {"analytic-models/AM_E.json": _body("AM_E"), "local-tables/LOCAL_T.json": _body("LOCAL_T")}))
    assert v["total_views"] == 0 and v["views"] == [] and (v["graphical_view_pct"], v["sql_view_pct"], v["unknown_view_pct"]) == (None, None, None)


# ── 6. lo que NO es una vista no contamina ──────────────────────────────────
def test_analytic_models_tables_and_flows_are_never_counted_with_family_info(tmp_path):
    ents = pool(ent("AM_F", up=["V_OK", "T_LOCAL", "T_REMOTE", "AM_INNER"], cube=True), ent("V_OK"), ent("T_LOCAL"), ent("T_REMOTE"), ent("AM_INNER", cube=True))
    ev = _evidence(tmp_path, {
        "views/V_OK.json": _body("V_OK", SQL_MARK), "local-tables/T_LOCAL.json": _body("T_LOCAL", SQL_MARK), "remote-tables/T_REMOTE.json": _body("T_REMOTE", SQL_MARK),
        "analytic-models/AM_INNER.json": _body("AM_INNER", graphical=True), "analytic-models/AM_F.json": _body("AM_F", SQL_MARK),
        "data-flows/DF_X.json": {"dataflows": {"DF_X": {"kind": "dataflow", "sources": {}, "targets": {}}}}})
    v = _fact(ents, "AM_F", ev)
    assert [r["technical_name"] for r in v["views"]] == ["V_OK"] and v["total_views"] == 1


def test_flat_pools_use_only_the_exporter_markers_and_exclude_non_view_families(tmp_path):
    ents = pool(ent("AM_P", up=["V_SQL", "V_GR", "V_PLAIN", "T_LOCAL", "T_REMOTE", "AM_IN"], cube=True), ent("V_SQL"), ent("V_GR"), ent("V_PLAIN"),
                ent("T_LOCAL", repository_type="DWC_LOCAL_TABLE"), ent("T_REMOTE", remote_entity="ABAPTABLES.X"), ent("AM_IN", cube=True))
    ev = _evidence(tmp_path, {                                           # SIN carpetas-tipo (export histórico plano)
        "V_SQL.json": _body("V_SQL", SQL_MARK), "V_GR.json": _body("V_GR", graphical=True), "V_PLAIN.json": _body("V_PLAIN"),
        "T_LOCAL.json": _body("T_LOCAL", SQL_MARK), "T_REMOTE.json": _body("T_REMOTE", SQL_MARK), "AM_IN.json": _body("AM_IN", SQL_MARK), "AM_P.json": _body("AM_P")})
    v = _fact(ents, "AM_P", ev)
    assert {r["technical_name"]: (r["view_type"], r["membership_basis"]) for r in v["views"]} == {"V_GR": ("GRAPHICAL", "STRUCTURAL_MARKER"), "V_SQL": ("SQL", "STRUCTURAL_MARKER")}
    # sin carpeta-tipo y sin marcador la vista NO es reconocible (límite explícito: UNKNOWN solo es observable con familia informada)
    assert "V_PLAIN" not in [r["technical_name"] for r in v["views"]]


def test_membership_never_depends_on_file_names(tmp_path):
    ents = pool(ent("AM_N", up=["VIEW_LOOKING_NAME"], cube=True), ent("VIEW_LOOKING_NAME"))
    ev = _evidence(tmp_path, {"VIEW_LOOKING_NAME.json": _body("VIEW_LOOKING_NAME")})
    assert view_membership(ev.view_type_evidence("VIEW_LOOKING_NAME"), ents["VIEW_LOOKING_NAME"], "VIEW_LOOKING_NAME", AssessmentUniverse.from_entities(ents)) is None


# ── 7-8. documento público, Markdown y HTML ─────────────────────────────────
def _inbound(root: Path) -> Path:
    _write(root, "Acme/remote-tables/RAW_T.json", {"definitions": {"RAW_T": {"kind": "entity", "@DataWarehouse.remote.connection": "S4",
                                                                           "@DataWarehouse.remote.entity": "ABAPTABLES.T1", "elements": {"A": {"type": "cds.String"}}}}})
    unk = _view("1VR_UNK", "RAW_T", ("A",))
    gra = _view("2VG_GR", "1VR_UNK", ("A",)); gra["editorSettings"] = {"2VG_GR": {"editor": {"default": "GRAPHICALVIEWBUILDER"}}}
    sql = _view("3VF_SQL", "2VG_GR", ("A",), pattern="ANALYTICAL_FACT"); sql["definitions"]["3VF_SQL"]["@DataWarehouse.sqlEditor.query"] = "SELECT A FROM \"2VG_GR\""
    _write(root, "Acme/views/1VR_UNK.json", unk)
    _write(root, "Acme/views/2VG_GR.json", gra)
    _write(root, "Acme/views/3VF_SQL.json", sql)
    _write(root, "Acme/analytic-models/4AM_T.json", _view("4AM_T", "3VF_SQL", ("A",), pattern="ANALYTICAL_CUBE", category="CUBE"))
    return root


@pytest.fixture()
def doc(tmp_path):
    d, _meta = shadow.assess(_inbound(tmp_path / "pool"), "Acme", "4AM_T")
    return d


def _am_highlights(d):
    return {h["id"]: h for a in d["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"]}


def test_json_exposes_the_composition_as_objective_facts(doc):
    h = _am_highlights(doc)
    assert h["views_in_closure"]["value"] == 3 and {b["name"]: b["count"] for b in h["views_in_closure"]["breakdown"]} == {"gráficas": 1, "SQL": 1, "sin clasificar": 1}
    assert {r["view_type"]: r["count"] for r in h["view_type_distribution"]["value"]} == {"GRAPHICAL": 1, "SQL": 1, "UNKNOWN": 1}
    assert all(abs(r["share"] - 1 / 3) < 1e-5 for r in h["view_type_distribution"]["value"])
    arts = {r["name"]: r for r in h["view_artifacts"]["value"]}
    assert set(arts) == {"1VR_UNK", "2VG_GR", "3VF_SQL"} and {r["view_type_basis"] for r in arts.values()} == {"DERIVED"}
    assert "am.view_type_composition@4AM_T" in {x["fact"] for x in doc["fact_index"]}


def test_markdown_and_html_view_show_the_same_metrics(doc):
    md = render_md.render_markdown(doc)
    sentence = "El cierre semántico contiene 3 vistas: 1 gráfica, 1 SQL y 1 sin clasificación."
    assert md.count(sentence) == 1                                                                 # UN solo hogar: el resumen transversal de la sección (no el área)
    assert "Vistas de Datasphere en el cierre (únicas)" not in md and "gráficas: count 1, proporción" not in md   # los totales no se repiten bajo Modelo analítico
    assert md.index(sentence) < md.index("### Modelo analítico")                                    # ...y van ANTES de la primera área
    from webapp.services import assessment_presenter as presenter
    from webapp.services import assessment_service as svc
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_T"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_T", generated_at="2026-10-04T00:00:00+00:00", profile=doc)
    view = presenter.report_view(status, entry, "datasphere_core")
    area = next(a for mg in view["macro_groups"] for a in mg["areas"] if a["id"] == "ANALYTIC_MODEL")
    assert area["facts"]["notes"] == []                                                           # el área ya no repite la composición del cierre
    tables = {t["title"]: t for g in area["facts"]["groups"] for t in g["tables"]}
    assert "Vistas de Datasphere en el cierre (únicas)" not in tables and "Distribución por tipo de vista" not in tables
    assert area["facts"]["closure_views"] == {"total": 3, "graphical": 1, "sql": 1, "unknown": 1, "structural": False}   # lo consume el resumen de la sección
    group = next(mg for mg in view["macro_groups"] if any(a["id"] == "ANALYTIC_MODEL" for a in mg["areas"]))
    assert [t["label"] for t in group["view_overview"]["tiles"]] == ["Total de vistas", "Vistas gráficas", "Vistas SQL", "Sin clasificar"]
    assert [t["value"] for t in group["view_overview"]["tiles"]] == [3, 1, 1, 1]
    from webapp.main import templates
    html = templates.get_template("partials/catalog_assessment_report.html").render(v=view)
    assert 'data-block="view-overview"' in html and "3VF_SQL" in html and "GRAPHICAL" in html                 # el detalle por artefacto se conserva


# ── 18. perfiles anteriores (sin el hecho) siguen legibles ──────────────────
def test_legacy_profiles_without_the_fact_stay_readable_and_show_nothing_invented(doc):
    legacy = json.loads(json.dumps(doc))
    for a in legacy["areas"]:
        a["highlights"] = [h for h in a["highlights"] if h["id"] not in ("views_in_closure", "view_type_distribution", "view_artifacts")]
    legacy["fact_index"] = [x for x in legacy["fact_index"] if not x["fact"].startswith("am.view_type_composition")]
    from tools.catalog.modeling_assessment import profile as ma_profile
    ma_profile.validate_assessment(legacy)
    assert "contiene" not in render_md.render_markdown(legacy).split("Vistas")[0] and "vistas:" not in render_md.render_markdown(legacy)
    from webapp.services import assessment_presenter as presenter
    from webapp.services import assessment_service as svc
    entry = type("E", (), {"entry_id": "e", "cliente": "Acme", "dominio": "General", "artefactos": ["4AM_T"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Acme", "4AM_T", generated_at="2026-10-04T00:00:00+00:00", profile=legacy)
    view = presenter.report_view(status, entry, "datasphere_core")
    area = next(a for mg in view["macro_groups"] for a in mg["areas"] if a["id"] == "ANALYTIC_MODEL")
    assert area["facts"]["notes"] == [] and area["facts"]["closure_views"] is None and "Vistas del cierre (nombre técnico y tipo)" not in [g["title"] for g in area["facts"]["groups"]]   # ausente ≠ cero vistas
    assert all(mg["view_overview"] is None for mg in view["macro_groups"])


# ── 19-20. sin juicio, sin puntaje, sin tocar versiones/cobertura/veredicto legacy ──────────────────
def test_the_fact_is_descriptive_only_and_does_not_move_coverage_signals_or_versions(doc, tmp_path):
    from tools.catalog.modeling_assessment import assessability as A
    spec = next(s for s in A.SURFACE_REGISTRY if s.surface_id == "am.view_type_composition")
    assert spec.role is A.SurfaceRole.DERIVED_VIEW                                                # no es superficie requerida: la cobertura no se mueve
    assert not any("VIEW_TYPE" in g["id"] or "SQL_VIEW" in g["id"] for g in (*doc["observations"], *doc["review_required"], *doc["descriptive_signals"]))
    assert set(doc["versions"]) == {"signals", "assessability", "confidence", "reason_codes", "finding_evidence", "identity", "facts_profile", "presentation_order"}
    assert doc["schema_version"] == "modeling_assessment.assessment/v1"                              # sin bump de esquema ni de versiones
    text = json.dumps(doc).lower()
    assert "penalt" not in text and "maturity" not in text
    legacy_src = (Path(__file__).resolve().parent.parent / "tools" / "catalog" / "generate_catalog.py").read_text(encoding="utf-8")
    assert "view_type" not in legacy_src and "viewType" not in legacy_src                         # el evaluador legacy no se toca


def test_pool_without_view_evidence_collection_keeps_previous_behavior(tmp_path):
    ev = SupplementaryEvidence.from_files([_write(tmp_path, "views/X.json", _body("X", SQL_MARK))], root=tmp_path)
    assert ev.view_type_evidence("X") is None                                                    # sin clasificador inyectado no se recoge evidencia


# ── redacción en pools PLANOS: UNKNOWN no es exhaustivamente observable ─────
def _flat_inbound(root: Path) -> Path:
    _write(root, "Flat/RAW_T.json", {"definitions": {"RAW_T": {"kind": "entity", "@DataWarehouse.remote.connection": "S4",
                                                              "@DataWarehouse.remote.entity": "ABAPTABLES.T1", "elements": {"A": {"type": "cds.String"}}}}})
    _write(root, "Flat/1VR_PLAIN.json", _view("1VR_PLAIN", "RAW_T", ("A",)))                                # vista SIN marcador: en un pool plano no es detectable
    gra = _view("2VG_GR", "1VR_PLAIN", ("A",)); gra["editorSettings"] = {"2VG_GR": {"editor": {"default": "GRAPHICALVIEWBUILDER"}}}
    sql = _view("3VF_SQL", "2VG_GR", ("A",), pattern="ANALYTICAL_FACT"); sql["definitions"]["3VF_SQL"]["@DataWarehouse.sqlEditor.query"] = "SELECT 1"
    _write(root, "Flat/2VG_GR.json", gra)
    _write(root, "Flat/3VF_SQL.json", sql)
    _write(root, "Flat/4AM_T.json", _view("4AM_T", "3VF_SQL", ("A",), pattern="ANALYTICAL_CUBE", category="CUBE"))
    return root


def test_flat_pool_wording_does_not_claim_zero_unknown_as_exhaustive(tmp_path):
    d, _meta = shadow.assess(_flat_inbound(tmp_path / "pool"), "Flat", "4AM_T")
    h = _am_highlights(d)
    assert h["views_in_closure"]["value"] == 2 and {r["membership_basis"] for r in h["view_artifacts"]["value"]} == {"STRUCTURAL_MARKER"}   # hechos intactos
    assert {b["name"]: b["count"] for b in h["views_in_closure"]["breakdown"]}["sin clasificar"] == 0                                         # el JSON sigue siendo factual
    md = render_md.render_markdown(d)
    assert "Se identificaron 2 vistas en el cierre semántico: 1 gráfica y 1 SQL." in md and md.count("Se identificaron 2 vistas") == 1
    assert "sin clasificación" not in md.split("Se identificaron")[1].split("\n")[0]                  # no se afirma "0 sin clasificación"
    assert "solo se reconocen por su marcador gráfico o SQL" in md
    from webapp.services import assessment_presenter as presenter
    from webapp.services import assessment_service as svc
    entry = type("E", (), {"entry_id": "e", "cliente": "Flat", "dominio": "General", "artefactos": ["4AM_T"]})()
    status = svc.AssessmentStatus(svc.CURRENT, "e", "Flat", "4AM_T", generated_at="2026-10-04T00:00:00+00:00", profile=d)
    area = next(a for mg in presenter.report_view(status, entry, "datasphere_core")["macro_groups"] for a in mg["areas"] if a["id"] == "ANALYTIC_MODEL")
    group = next(mg for mg in presenter.report_view(status, entry, "datasphere_core")["macro_groups"] if any(a["id"] == "ANALYTIC_MODEL" for a in mg["areas"]))
    ov = group["view_overview"]
    assert [t["label"] for t in ov["tiles"]] == ["Total de vistas", "Vistas gráficas", "Vistas SQL"]            # sin tile de "Sin clasificar" en 0
    assert ov["caveat"] == render_md.STRUCTURAL_VIEWS_CAVEAT and "no informa carpetas por tipo" in ov["caveat"]    # pool plano: salvedad breve, jamás un "0 sin clasificar"


def test_wording_by_membership_basis_authoritative_structural_and_mixed():
    bd = [{"name": "gráficas", "count": 13}, {"name": "SQL", "count": 3}, {"name": "sin clasificar", "count": 0}]
    assert render_md.view_composition_text(16, bd) == "El cierre semántico contiene 16 vistas: 13 gráficas, 3 SQL y 0 sin clasificación."       # familia: sin cambios
    structural = render_md.view_composition_text(61, [{"name": "gráficas", "count": 31}, {"name": "SQL", "count": 30}, {"name": "sin clasificar", "count": 0}], structural=True)
    assert structural.startswith("Se identificaron 61 vistas en el cierre semántico: 31 gráficas y 30 SQL.") and "0 sin clasificación" not in structural
    mixed = render_md.view_composition_text(5, [{"name": "gráficas", "count": 2}, {"name": "SQL", "count": 2}, {"name": "sin clasificar", "count": 1}], structural=True)
    assert "2 gráficas, 2 SQL y 1 sin clasificación (entre las identificadas por carpeta)" in mixed       # prudente: UNKNOWN solo viene de las identificadas por carpeta
    assert render_md.has_structural_membership([{"id": "view_artifacts", "value": [{"membership_basis": "FAMILY_DIRECTORY"}, {"membership_basis": "STRUCTURAL_MARKER"}]}])
    assert not render_md.has_structural_membership([{"id": "view_artifacts", "value": [{"membership_basis": "FAMILY_DIRECTORY"}]}]) and not render_md.has_structural_membership([])
