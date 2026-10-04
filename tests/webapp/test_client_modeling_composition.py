"""test_client_modeling_composition.py — "Composición del modelado" del Assessment del cliente (Gate 4).

Dos lecturas DISTINTAS que no se suman:
  A. Inventario técnico disponible — artefactos ÚNICOS del pool de crudos del cliente (no es el tenant);
  B. Participación en los Assessments VIGENTES — unión única de las vistas de los cierres, solo si TODOS los CURRENT traen el hecho.
Un solo snapshot alimenta JSON, Markdown y HTML; sin juicio de calidad; 0 observado ≠ no disponible; la suma de ocurrencias (465 en el caso Biogenesis)
no es jamás un conteo de objetos únicos. Fixtures SINTÉTICAS (la forma del caso Biogenesis: 149 únicas = 115 gráficas + 33 SQL + 1 sin clasificar).
"""
from __future__ import annotations

import inspect
import json
import re
import types

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tests._modeling_assessment_m7_fixtures import _view, _write
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as pf
from webapp.services import client_modeling_inventory as inv

PACK = "datasphere_core"
HX = {"HX-Request": "true"}
SQL_KEY = "@DataWarehouse.sqlEditor.query"
BANNED = re.compile(r"\b(score|scores|scoring|puntaje|puntajes|puntuaci[oó]n|madurez|maturity|banda|bandas|PLENO|BUENO|PARCIAL|malo|mala|bueno|buena|mejor|peor)\b", re.I)
OVERCLAIM = re.compile(r"todo el landscape|todos los objetos del cliente|total del tenant\b(?! de Datasphere\))", re.I)


# ── pool sintético ──────────────────────────────────────────────────────────
def _g(name):
    v = _view(name, "RAW_T", ("A",))
    v["editorSettings"] = {name: {"editor": {"default": "GRAPHICALVIEWBUILDER"}}}
    return v


def _s(name):
    v = _view(name, "RAW_T", ("A",))
    v["definitions"][name][SQL_KEY] = "SELECT 1"
    return v


def _remote(name):
    return {"definitions": {name: {"kind": "entity", "@DataWarehouse.remote.connection": "S4", "@DataWarehouse.remote.entity": "T1",
                                   "elements": {"A": {"type": "cds.String"}}}}}


def _acme_pool(root):
    _write(root, "Acme/remote-tables/RAW_T.json", _remote("RAW_T"))
    _write(root, "Acme/views/V_G1.json", _g("V_G1"))
    _write(root, "Acme/views/V_G2.json", _g("V_G2"))
    _write(root, "Acme/views/V_S1.json", _s("V_S1"))
    _write(root, "Acme/views/V_S2.json", _s("V_S2"))
    _write(root, "Acme/views/V_U1.json", _view("V_U1", "RAW_T", ("A",)))
    _write(root, "Acme/Dom2/views/V_S1.json", _s("V_S1"))                              # la MISMA vista, otro dominio del pool: cuenta una vez
    _write(root, "Acme/views/V_STORED.json", {**_s("V_STORED"), "viewType": "GRAPHICAL"})   # tipo ALMACENADO (gana sobre la derivación)
    _write(root, "Acme/analytic-models/4AM_X.json", _s("4AM_X"))                        # un AM con query NO es una vista
    _write(root, "Acme/replication-flows/RF_1.json", {"replicationflows": {"RF_1": {}}})
    _write(root, "Acme/replication-flows/RF_2.json", {"replicationflows": {"RF_2": {}}})
    _write(root, "Acme/data-flows/DF_1.json", {"dataflows": {"DF_1": {}}})
    return root


def _flat_pool(root):
    _write(root, "Flat/RAW_T.json", _remote("RAW_T"))
    _write(root, "Flat/V_S1.json", _s("V_S1"))
    _write(root, "Flat/V_G1.json", _g("V_G1"))
    _write(root, "Flat/V_PLAIN.json", _view("V_PLAIN", "RAW_T", ("A",)))               # sin marcador: en un pool plano no es detectable
    _write(root, "Flat/RF_X.json", {"replicationflows": {"RF_X": {}}})                  # plano: la familia no se distingue
    return root


def _beta_pool(root):
    _write(root, "Beta/views/B1.json", _s("B1"))
    _write(root, "Beta/views/B2.json", _s("B2"))
    _write(root, "Beta/views/B3.json", _s("B3"))
    _write(root, "Beta/views/B4.json", _g("B4"))
    return root


# ── perfiles / estados ──────────────────────────────────────────────────────
def _entry(eid, cliente, am):
    return types.SimpleNamespace(entry_id=eid, cliente=cliente, dominio="Dom", veredicto="buena", artefactos=[am], path=f"/x/{eid}.md")


def _profile(views=None):
    p = {"assessment_state": "ASSESSED", "schema_version": "modeling_assessment.assessment/v1", "scope": {"root_type": "analytic-model"},
         "summary": {"coverage": {"strict": 0.9}, "confidence": {"level": "HIGH"}}, "observations": [], "review_required": [], "areas": []}
    if views is not None:                                                                # None = perfil ANTERIOR al hecho (legible, sin el dato)
        hl = [{"id": "views_in_closure", "label": "x", "value": len(views)}]
        if views:
            hl.append({"id": "view_artifacts", "label": "x", "value": [{"membership_basis": "FAMILY_DIRECTORY", "name": n, "view_type": t, "view_type_basis": "DERIVED"}
                                                                       for n, t in views]})
        p["areas"] = [{"id": "ANALYTIC_MODEL", "highlights": hl}]
    return p


def _status(entry, profile, state=svc.CURRENT):
    return svc.AssessmentStatus(state, entry.entry_id, entry.cliente, entry.artefactos[0], profile=profile, generated_at="2026-10-04T10:00:00+00:00")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    pool = tmp_path / "pool"
    for build in (_acme_pool, _flat_pool, _beta_pool):
        build(pool)
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(pool))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    inv._memo.clear()
    state = types.SimpleNamespace(entries=[], statuses={}, pool=pool)
    monkeypatch.setattr(catalog_reads, "_catalog_entries",
                        lambda: (types.SimpleNamespace(find_flow_by_id=lambda es, i: next((x for x in es if x.entry_id == i), None)), state.entries))
    monkeypatch.setattr(svc, "statuses_for_entries", lambda batch: {x.entry_id: state.statuses[x.entry_id] for x in batch})

    def add(cliente, n, profile_of, state_of=svc.CURRENT):
        for i in range(n):
            e = _entry(f"{cliente}{i}", cliente, f"AM_{cliente}_{i}")
            state.entries.append(e)
            state.statuses[e.entry_id] = _status(e, profile_of(i), state_of)
        return state.entries[-n]
    state.add = add
    yield state
    inv._memo.clear()


def _client():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "u@danwor.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _comp(env, first):
    return pf.build_snapshot(first, PACK)["modeling_composition"]


# ── A. inventario técnico disponible ────────────────────────────────────────
def test_inventory_views_are_deduplicated_and_typed_by_the_single_classifier(env):
    v = inv.inventory_for("Acme")["views"]
    # G1 G2 S1 S2 U1 STORED = 6 únicas (V_S1 aparece en dos dominios y cuenta una; el AM con query y la tabla remota NO son vistas)
    assert (v["total"], v["graphical"], v["sql"], v["unknown"]) == (6, 3, 2, 1)
    assert v["graphical"] + v["sql"] + v["unknown"] == v["total"]                                  # invariante
    assert v["view_type_basis"] == {"stored": 1, "derived": 5}                                       # el tipo almacenado gana (STORED), el resto se deriva
    assert v["membership"] == {"family_directory": 6, "structural_marker": 0} and v["unknown_detection"] == inv.EXHAUSTIVE


def test_the_inventory_has_no_classifier_of_its_own():
    src = inspect.getsource(inv) + inspect.getsource(pf)
    assert "GRAPHICALVIEWBUILDER" not in src and SQL_KEY not in src and "sqlEditor" not in src
    from webapp.services import catalog_view_source as cvs
    assert "dsp_view_type" in inspect.getsource(cvs)                                                 # la fuente única, vía la misma pertenencia que "Abrir SQL"


def test_identity_is_client_family_and_technical_name(env):
    out = inv.inventory_for("Acme")
    assert out["identity_basis"] == "CLIENT_FAMILY_NAME"
    assert inv.inventory_for("acme") == out                                                          # el alcance de cliente no distingue mayúsculas
    assert inv.inventory_for("Beta")["views"]["total"] == 4                                          # otro cliente: su propio inventario, sin mezclar


def test_flow_families_observed_counts_and_null_when_not_proven(env):
    out = inv.inventory_for("Acme")
    assert (out["replication_flows"], out["data_flows"]) == (2, 1)
    assert out["transformation_flows"] is None and out["task_chains"] is None                       # sin archivos ni evidencia de adquisición: NO es 0
    assert out["flow_evidence"] == {"replication_flows": "OBSERVED", "data_flows": "OBSERVED", "transformation_flows": "UNAVAILABLE", "task_chains": "UNAVAILABLE"}


def test_zero_is_reported_only_when_acquisition_evidence_proves_it(env):
    _write(env.pool, "Acme/lineage-manifest.json", {"effectiveFeatures": ["data-flows", "replication-flows", "tables", "transformation-flows", "views"]})
    out = inv.inventory_for("Acme")
    assert out["transformation_flows"] == 0 and out["flow_evidence"]["transformation_flows"] == "ACQUIRED_ZERO"   # adquirida y sin artefactos
    assert out["task_chains"] is None and out["flow_evidence"]["task_chains"] == "UNAVAILABLE"                  # no adquirida: sigue sin disponible
    assert (out["replication_flows"], out["views"]["total"]) == (2, 6)                                         # el manifiesto no suma objetos


def test_flat_pools_use_structural_markers_with_the_accepted_epistemic_limit(env):
    out = inv.inventory_for("Flat")
    assert (out["views"]["total"], out["views"]["graphical"], out["views"]["sql"], out["views"]["unknown"]) == (2, 1, 1, 0)   # V_PLAIN no es detectable
    assert out["views"]["unknown_detection"] == inv.PARTIAL and out["views"]["membership"]["structural_marker"] == 2
    assert out["replication_flows"] is None                                                           # en un pool plano la familia no se distingue
    env.add("Flat", 1, lambda i: _profile([]))
    story = pf.report_view(pf.build_snapshot(env.entries[-1], PACK))["composition"]["inventory"]
    assert story["limited_unknown"] and "0 sin clasificación" not in story["story"]
    assert story["unknown_caveat"] == pf.UNKNOWN_PARTIAL_CAVEAT                                         # la salvedad es explícita y breve


def test_unavailable_pool_is_not_zero(env):
    out = inv.inventory_for("Nadie")
    assert out["available"] is False and out["views"]["total"] is None and out["replication_flows"] is None
    env.add("Nadie", 1, lambda i: _profile(None))
    comp = pf.report_view(pf.build_snapshot(env.entries[-1], PACK))["composition"]["inventory"]
    assert [k["value"] for k in comp["kpis"]] == ["No disponible"] * 4 and comp["show_tiles"] is False and comp["note"].startswith("No disponible")


# ── B. participación en los Assessments vigentes ────────────────────────────
NAMES = [f"V{i:03d}" for i in range(149)]
TYPES = {n: ("GRAPHICAL" if i < 115 else "SQL" if i < 148 else "UNKNOWN") for i, n in enumerate(NAMES)}


def _closure_views(i):
    """25 cierres con 465 ocurrencias en total sobre 149 vistas únicas (cierre 0 = todas; 4 de los otros 24 con 14 y 20 con 13)."""
    if i == 0:
        names = NAMES
    else:
        size = 14 if i <= 4 else 13
        names = [NAMES[(i * 7 + k) % 149] for k in range(size)]
    return [(n, TYPES[n]) for n in names]


def test_the_biogenesis_shape_149_unique_views_come_from_465_occurrences(env):
    assert sum(len(_closure_views(i)) for i in range(25)) == 465
    first = env.add("Bio", 25, lambda i: _profile(_closure_views(i)))
    sc = _comp(env, first)["current_assessment_scope"]
    assert sc["available"] and sc["enriched_assessments"] == sc["total_current_assessments"] == 25
    assert (sc["unique_views"], sc["graphical"], sc["sql"], sc["unknown"]) == (149, 115, 33, 1)
    assert sc["graphical"] + sc["sql"] + sc["unknown"] == sc["unique_views"]
    assert sc["view_occurrences"] == 465                                                              # dato interno de procedencia, NO un conteo de objetos


def test_465_never_appears_as_a_unique_view_metric_in_any_surface(env):
    first = env.add("Bio", 25, lambda i: _profile(_closure_views(i)))
    snap = pf.build_snapshot(first, PACK)
    md, view = pf.to_markdown(snap), pf.report_view(snap)
    html = _client_html(env, first)
    for kpi in view["composition"]["scope"]["kpis"] + view["composition"]["inventory"]["kpis"]:
        assert kpi["value"] != "465"
    assert "465" not in md and "465" not in html and "view_occurrences" not in md and "view_occurrences" not in html
    assert [k["value"] for k in view["composition"]["scope"]["kpis"]] == ["149", "115", "33", "1"]


def test_the_same_view_in_many_closures_counts_once_and_totals_are_never_summed(env):
    shared = [("V_G1", "GRAPHICAL"), ("V_S1", "SQL")]
    first = env.add("Acme", 3, lambda i: _profile(shared + ([("V_U1", "UNKNOWN")] if i == 2 else [])))
    sc = _comp(env, first)["current_assessment_scope"]
    assert (sc["unique_views"], sc["graphical"], sc["sql"], sc["unknown"]) == (3, 1, 1, 1) and sc["view_occurrences"] == 7


def test_partial_enrichment_is_not_available_never_a_partial_union(env):
    first = env.add("Acme", 3, lambda i: _profile([("V_G1", "GRAPHICAL")]) if i < 2 else _profile(None))     # 2 de 3 traen el hecho
    sc = _comp(env, first)["current_assessment_scope"]
    assert sc["available"] is False and (sc["enriched_assessments"], sc["total_current_assessments"]) == (2, 3)
    assert sc["unique_views"] is None and sc["graphical"] is None and sc["sql"] is None and sc["unknown"] is None and sc["view_occurrences"] is None
    html = _client_html(env, first)
    assert "solo 2 de 3 assessments vigentes" in html
    comp = pf.report_view(pf.build_snapshot(first, PACK))["composition"]["scope"]
    assert [k["value"] for k in comp["kpis"]] == ["No disponible"] * 4 and "<table data-block=\"scope-distribution\"" not in html


def test_non_current_assessments_do_not_contribute_nor_block_completeness(env):
    first = env.add("Acme", 2, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    stale = _entry("stale", "Acme", "AM_STALE")
    env.entries.append(stale)
    env.statuses["stale"] = _status(stale, _profile([("V_S9", "SQL")]), svc.STALE)
    sc = _comp(env, first)["current_assessment_scope"]
    assert sc["available"] and (sc["total_current_assessments"], sc["unique_views"]) == (2, 1)       # el STALE ni suma ni bloquea


def test_legacy_current_profiles_without_the_fact_remain_readable(env):
    first = env.add("Acme", 2, lambda i: _profile(None))
    snap = pf.build_snapshot(first, PACK)
    assert snap["summary"]["current_assessments"] == 2 and snap["summary"]["coverage"]["average_strict"] == pytest.approx(0.9)
    assert snap["modeling_composition"]["current_assessment_scope"]["available"] is False
    assert pf.to_markdown(snap) and _client_html(env, first)


def test_no_current_assessments_means_not_available_without_inventing_a_zero(env):
    first = env.add("Acme", 1, lambda i: _profile([("V_G1", "GRAPHICAL")]), state_of=svc.NOT_YET_ASSESSED)
    sc = _comp(env, first)["current_assessment_scope"]
    assert sc["available"] is False and sc["total_current_assessments"] == 0 and sc["unique_views"] is None
    assert "No hay assessments vigentes" in pf.report_view(pf.build_snapshot(first, PACK))["composition"]["scope"]["note"]


# ── paridad, vocabulario y no regresión ─────────────────────────────────────
def _client_html(env, first):
    r = _client().get(f"/catalog/client/assessment?flow={first.entry_id}&pack={PACK}", headers=HX)
    assert r.status_code == 200
    return r.text


def test_json_markdown_and_html_agree_because_they_share_one_snapshot(env):
    first = env.add("Acme", 3, lambda i: _profile([("V_G1", "GRAPHICAL"), ("V_S1", "SQL"), ("V_U1", "UNKNOWN")]))
    c = _client()
    html = c.get(f"/catalog/client/assessment?flow={first.entry_id}&pack={PACK}", headers=HX).text
    snap = c.get(f"/catalog/client/assessment.json?flow={first.entry_id}").json()
    md = c.get(f"/catalog/client/assessment.md?flow={first.entry_id}").text
    assert md == pf.to_markdown(snap)
    mc = snap["modeling_composition"]
    view = pf.report_view(snap)["composition"]
    for kpis in (view["inventory"]["kpis"], view["scope"]["kpis"]):
        for k in kpis:
            assert f"- **{k['label']}:** {k['value']}" in md and f'<span class="activity-kpi__value">{k["value"]}</span><span class="activity-kpi__label">{k["label"]}</span>' in html
    for f in view["inventory"]["flows"]:
        assert f"- **{f['label']}:** {f['value']}" in md and f"<td>{f['label']}</td><td>{f['value']}</td>" in html
    assert mc["inventory"]["views"]["total"] == 6 and mc["current_assessment_scope"]["unique_views"] == 3
    assert "Inventario técnico disponible" in html and "Participación en Assessments vigentes" in html and "Composición del modelado" in html
    assert "## Composición del modelado" in md and "### Inventario técnico disponible" in md and "### Participación en Assessments vigentes" in md
    assert list(json.loads(pf.to_json(snap))["modeling_composition"]) == ["current_assessment_scope", "inventory"]      # orden determinista


def test_unavailable_flow_families_render_as_not_available_never_as_zero(env):
    first = env.add("Acme", 1, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    flows = {f["label"]: f["value"] for f in pf.report_view(pf.build_snapshot(first, PACK))["composition"]["inventory"]["flows"]}
    assert flows == {"Replication Flows": "2", "Data Flows": "1", "Transformation Flows": "No disponible", "Task Chains": "No disponible"}
    html = _client_html(env, first)
    assert '<tr data-family="task_chains" data-evidence="UNAVAILABLE"><td>Task Chains</td><td>No disponible</td></tr>' in html
    assert '<td>Task Chains</td><td>0</td>' not in html and '<td>Transformation Flows</td><td>0</td>' not in html
    assert 'activity-kpi__value">No disponible' not in html                                         # nunca como valor gigante de un tile


def test_the_report_is_data_driven_not_client_specific(env):
    beta = env.add("Beta", 1, lambda i: _profile([("B1", "SQL"), ("B4", "GRAPHICAL")]))
    acme = env.add("Acme", 1, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    b = pf.report_view(pf.build_snapshot(beta, PACK))["composition"]["inventory"]
    a = pf.report_view(pf.build_snapshot(acme, PACK))["composition"]["inventory"]
    assert [k["value"] for k in b["kpis"]] == ["4", "1", "3", "0"] and "mayoritariamente SQL" in b["story"]
    assert [k["value"] for k in a["kpis"]] == ["6", "3", "2", "1"] and "mixta, sin un tipo mayoritario" in a["story"]


def test_the_narrative_is_neutral_no_judgment_no_score_no_overclaim(env):
    first = env.add("Acme", 2, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    snap = pf.build_snapshot(first, PACK)
    html = re.sub(r"<[^>]+>", " ", _client_html(env, first))
    section = html[html.index("Composición del modelado"):]
    md = pf.to_markdown(snap)
    md_section = md[md.index("## Composición del modelado"):]
    for text in (section, md_section):
        assert not BANNED.search(text), BANNED.search(text).group(0)
        assert not OVERCLAIM.search(text), OVERCLAIM.search(text).group(0)
    assert snap["summary"].keys() == {"current_assessments", "requires_review", "coverage", "confidence_distribution"}   # sin métricas metodológicas nuevas


def test_existing_portfolio_semantics_survive(env):
    first = env.add("Acme", 3, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    snap = pf.build_snapshot(first, PACK)
    view = pf.report_view(snap)
    assert [k["label"] for k in view["kpis"]] == ["Total de Artefactos", "Assessments vigentes", "Requieren revisión", "Cobertura promedio"]
    assert [k["value"] for k in view["kpis"]] == ["3", "3", "0", "90.0%"]
    assert snap["schema_version"] == "client-assessment-portfolio/v1" and "confidence_distribution" in snap["summary"] and "recurring_findings" in snap


def test_opening_the_portfolio_never_extracts_nor_regenerates(env, monkeypatch):
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: pytest.fail("el Portfolio no extrae de Datasphere"))
    monkeypatch.setattr(svc, "generate", lambda *a, **k: pytest.fail("no genera assessments"))
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: pytest.fail("no ejecuta el motor"))
    monkeypatch.setattr(svc.ma_shadow, "assess", lambda *a, **k: pytest.fail("no ejecuta el Modeling Assessment"))
    first = env.add("Acme", 2, lambda i: _profile(None))
    assert _client_html(env, first)
    assert _client().get(f"/catalog/client/assessment.json?flow={first.entry_id}").status_code == 200


def test_the_inventory_is_read_only_and_memoized_by_pool_fingerprint(env):
    before = sorted(p.as_posix() for p in env.pool.rglob("*"))
    a = inv.inventory_for("Acme")
    assert inv.inventory_for("Acme") == a and sorted(p.as_posix() for p in env.pool.rglob("*")) == before   # no escribe ni crea nada
    _write(env.pool, "Acme/views/V_NEW.json", _s("V_NEW"))
    assert inv.inventory_for("Acme")["views"]["total"] == a["views"]["total"] + 1                           # la huella invalida el memo


# ── claridad final: denominador explícito y salvedad de pools planos ────────
def test_participation_states_its_assessment_and_catalog_denominators(env):
    first = env.add("Acme", 1, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    extra = _entry("acme-pending", "Acme", "AM_PENDING")                                              # un artefacto del catálogo SIN assessment vigente
    env.entries.append(extra)
    env.statuses["acme-pending"] = _status(extra, None, svc.NOT_YET_ASSESSED)
    snap = pf.build_snapshot(first, PACK)
    note = pf.report_view(snap)["composition"]["scope"]["note"]
    assert note.startswith("Calculado sobre 1 assessment vigente de 2 artefactos del catálogo")
    assert "El restante no tiene un assessment vigente y no participa" in note and "no representa a todos los artefactos" in note
    html, md = _client_html(env, first), pf.to_markdown(snap)
    assert "Calculado sobre 1 assessment vigente de 2 artefactos del catálogo" in html and "Calculado sobre 1 assessment vigente de 2 artefactos del catálogo" in md


def test_the_denominator_distinguishes_all_current_enriched_from_all_catalog_covered(env):
    first = env.add("Acme", 3, lambda i: _profile([("V_G1", "GRAPHICAL")]))                            # los 3 del catálogo, los 3 vigentes y enriquecidos
    note = pf.report_view(pf.build_snapshot(first, PACK))["composition"]["scope"]["note"]
    assert note.startswith("Calculado sobre 3 assessments vigentes de 3 artefactos del catálogo") and "restantes" not in note and "no participa" not in note


def test_stale_and_missing_artifacts_are_not_called_assessments(env):
    first = env.add("Acme", 1, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    for eid, state in (("s", svc.STALE), ("n", svc.NOT_YET_ASSESSED), ("f", svc.FAILED)):
        e = _entry(eid, "Acme", f"AM_{eid.upper()}")
        env.entries.append(e)
        env.statuses[eid] = _status(e, _profile([("V_X", "SQL")]) if state == svc.STALE else None, state)
    note = pf.report_view(pf.build_snapshot(first, PACK))["composition"]["scope"]["note"]
    assert "Calculado sobre 1 assessment vigente de 4 artefactos del catálogo" in note and "Los 3 restantes no tienen un assessment vigente" in note


def test_partial_enrichment_keeps_not_available_with_the_x_of_y_explanation(env):
    first = env.add("Acme", 4, lambda i: _profile([("V_G1", "GRAPHICAL")]) if i < 3 else _profile(None))
    html = _client_html(env, first)
    assert "No disponible: solo 3 de 4 assessments vigentes incluyen la composición por tipo de vista" in html
    assert "Calculado sobre" not in html


def test_the_partial_unknown_detection_caveat_shows_only_for_partial_inventories(env):
    caveat = "La identificación de vistas sin clasificar es parcial porque este inventario no informa la familia de todos los artefactos."
    flat = env.add("Flat", 1, lambda i: _profile([]))
    snap = pf.build_snapshot(flat, PACK)
    assert snap["modeling_composition"]["inventory"]["views"]["unknown_detection"] == "PARTIAL"
    assert caveat in _client_html(env, flat) and caveat in pf.to_markdown(snap)
    assert snap["modeling_composition"]["inventory"]["views"]["unknown"] == 0                         # el conteo no se toca ni se inventan UNKNOWN
    acme = env.add("Acme", 1, lambda i: _profile([("V_G1", "GRAPHICAL")]))
    asnap = pf.build_snapshot(acme, PACK)
    assert asnap["modeling_composition"]["inventory"]["views"]["unknown_detection"] == "EXHAUSTIVE"
    assert "es parcial porque" not in _client_html(env, acme) and "es parcial porque" not in pf.to_markdown(asnap)
