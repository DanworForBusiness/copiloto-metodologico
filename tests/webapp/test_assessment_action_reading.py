"""Assessment — lectura orientada a acciones y evidencia completa de hallazgos (solo PRESENTACIÓN).

Fija:
  * el registro de etiquetas (`assessment_labels`) solo contiene códigos cuyo significado está en el propio motor (cada uno figura en el archivo fuente declarado) y ninguna etiqueta es el código;
  * la atribución de artefactos a familias de incumplimiento solo ocurre cuando la evidencia canónica la demuestra (grupo con UNA familia y conteo coincidente); nunca se reparte por nombre;
  * contra los Assessments gobernados REALES locales (niveles BUENA / ADECUADA / PARCIAL / No determinable, con y sin hallazgos): la guía de lectura enlaza solo a secciones existentes, los códigos
    del registro no aparecen en la lectura principal (solo en el glosario del detalle técnico y como `title`), cada artefacto de un hallazgo es accesible (sin «…(+N)» opaco), los nombres técnicos de
    las etiquetas compartidas se muestran completos y la presentación no altera el perfil ni el Judgment (el nivel mostrado es el persistido);
  * la afirmación «la gobernanza de nombres no cambia el nivel» tiene fundamento en el código del Judgment (no usa esos hallazgos).
Los perfiles reales viven en `governed-assessments/` (runtime, no versionado): sin ellos las pruebas de corpus se omiten; las unitarias corren siempre.
"""
from __future__ import annotations

import copy
import glob
import json
import re
from pathlib import Path

import pytest

from webapp.main import templates
from webapp.services import assessment_labels as AL
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from tools.catalog.modeling_assessment.presentation import naming_violation_family as _FAMILY_OF

def NV(doc):
    """`assessment_labels.naming_view` con el clasificador de la API pública (como lo inyecta el presenter)."""
    return AL.naming_view(doc, _FAMILY_OF)


REPO = Path(__file__).resolve().parents[2]
TEMPLATE = "partials/catalog_assessment_report.html"
CORPUS = sorted(glob.glob(str(REPO / "governed-assessments" / "v1" / "*" / "*.json")))


# ═══════════════════════════ registro ═══════════════════════════
@pytest.mark.parametrize("code", sorted(AL.REGISTRY))
def test_every_registry_code_is_defined_in_its_declared_engine_source(code):
    entry = AL.REGISTRY[code]
    text = (REPO / entry.source).read_text(encoding="utf-8")
    assert re.search(r"\b" + re.escape(code) + r"\b", text), f"{code} no figura en {entry.source}"
    assert entry.label != code and entry.label and entry.meaning


def test_family_meanings_rest_on_the_texts_the_engine_itself_maps_to_each_family():
    """El significado de cada familia sale de los textos de ``parse_name`` (``generate_catalog.py``) que ``naming_rules._ISSUE_FRAGMENTS`` asocia a la familia."""
    from tools.catalog.modeling_assessment import naming_rules as NR
    gc = (REPO / "tools" / "catalog" / "generate_catalog.py").read_text(encoding="utf-8")
    mapped = {fam.value for _frag, fam in NR._ISSUE_FRAGMENTS}
    assert mapped <= set(AL.REGISTRY)
    for fragment, _fam in NR._ISSUE_FRAGMENTS:
        assert fragment in gc, fragment


def test_labels_are_not_heuristic_translations_of_unknown_codes():
    assert AL.label("NOT_A_REAL_CODE") is None and AL.cell("NOT_A_REAL_CODE") == "NOT_A_REAL_CODE"      # un código sin entrada se muestra tal cual
    assert AL.cell("MISSING_LAYER_PREFIX") == {"text": "Tabla o vista sin prefijo de capa", "code": "MISSING_LAYER_PREFIX"}


def test_generic_words_are_only_labelled_in_their_own_context():
    assert AL.cell("UNKNOWN") == "UNKNOWN" and AL.label("SYSTEM") is None                                     # fuera de contexto: tal cual
    assert AL.cell("UNKNOWN", generic_ok=True)["text"] == "Procedencia no determinable"                       # en la tabla de procedencia: etiqueta
    text, used = AL.relabel("identidad: SOURCE_DERIVED_IMPORTED; estado UNKNOWN; capa SYSTEM; PROJECT_ASSIGNED_X")
    assert text == "identidad: nombre heredado de la fuente remota; estado UNKNOWN; capa SYSTEM; PROJECT_ASSIGNED_X" and used == ["SOURCE_DERIVED_IMPORTED"]   # palabra completa, sin tocar lo demás


def test_the_family_reason_comes_from_the_persisted_rule_text_via_the_engines_own_mapping():
    from tools.catalog.modeling_assessment import naming_rules as NR
    assert NR.violation_family("tabla/vista sin prefijo de capa (§28: <Capa><Artefacto><Uso>_)").value == "MISSING_LAYER_PREFIX"
    assert NR.violation_family("no sigue §28 <Capa><Artefacto><Uso>_ ni un artefacto homologado (DF/RF/FT/AM/…)").value == "NO_RECOGNIZED_PATTERN"


# ═══════════════════════════ atribución por familia ═══════════════════════════
def _doc(by_identity, histogram, non_compliant, compliant=("OK_1",)):
    return {"canonical_evidence": {"naming_evidence": {"hygiene": {"artifact_name_populations": {
        "section_28": {"applicable": len(non_compliant) + len(compliant), "non_compliant": len(non_compliant), "compliant": len(compliant), "non_compliant_artifacts": list(non_compliant),
                       "compliant_artifacts": list(compliant), "violation_family_histogram": histogram, "violation_reason_histogram": []},
        "by_identity": by_identity}}}}}


def test_a_single_family_group_with_a_matching_count_attributes_all_its_artifacts():
    doc = _doc([{"identity": "A", "non_compliant_artifacts": ["x1", "x2"], "violation_family_histogram": [{"key": "MISSING_LAYER_PREFIX", "count": 2}], "distinct_violation_families": 1},
                {"identity": "B", "non_compliant_artifacts": ["y1"], "violation_family_histogram": [{"key": "NO_RECOGNIZED_PATTERN", "count": 1}], "distinct_violation_families": 1}],
               [{"key": "MISSING_LAYER_PREFIX", "count": 2}, {"key": "NO_RECOGNIZED_PATTERN", "count": 1}], ["x1", "x2", "y1"])
    nv = NV(doc)
    assert nv["all_attributed"] and nv["unattributed"] == []
    assert {f["code"]: f["artifacts"] for f in nv["families"]} == {"MISSING_LAYER_PREFIX": ["x1", "x2"], "NO_RECOGNIZED_PATTERN": ["y1"]}


def test_a_mixed_family_group_is_never_split_by_guessing():
    doc = _doc([{"identity": "A", "non_compliant_artifacts": ["x1", "x2", "y1"],
                 "violation_family_histogram": [{"key": "MISSING_LAYER_PREFIX", "count": 2}, {"key": "NO_RECOGNIZED_PATTERN", "count": 1}], "distinct_violation_families": 2}],
               [{"key": "MISSING_LAYER_PREFIX", "count": 2}, {"key": "NO_RECOGNIZED_PATTERN", "count": 1}], ["x1", "x2", "y1"])
    nv = NV(doc)
    assert not nv["all_attributed"] and all(f["artifacts"] is None for f in nv["families"])        # solo el agregado: la plantilla lo dice
    assert nv["unattributed"] == ["x1", "x2", "y1"]


def test_a_count_that_does_not_match_the_group_is_not_attributed():
    doc = _doc([{"identity": "A", "non_compliant_artifacts": ["x1", "x2"], "violation_family_histogram": [{"key": "MISSING_LAYER_PREFIX", "count": 5}], "distinct_violation_families": 1}],
               [{"key": "MISSING_LAYER_PREFIX", "count": 5}], ["x1", "x2"])
    assert NV(doc)["families"][0]["artifacts"] is None


def test_profiles_without_section_28_keep_the_previous_reading():
    assert NV({}) is None and NV({"canonical_evidence": {}}) is None and NV(None) is None


# ═══════════════════════════ fundamento de «no cambia el nivel» ═══════════════════════════
def test_the_judgment_engine_does_not_consume_the_naming_findings_the_guide_says_it_ignores():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "tools" / "catalog" / "methodological_judgment").glob("*.py"))
    assert "FIELD_VOCABULARY_MIX" not in src and "same_normalized_label" not in src
    assert re.findall(r"groups\.get\(\"([A-Z_]+)\"\)", src) == ["DIRECT_RAW_TABLE_ACCESS"]                  # el único hallazgo de observaciones/casos que el motor lee
    rules = (REPO / "tools" / "catalog" / "methodological_judgment" / "rules.py").read_text(encoding="utf-8")
    assert re.search(r'"naming_governance".*qualifier_naming', rules) and '"level_effect": "NONE"' in rules


# ═══════════════════════════ corpus real ═══════════════════════════
def _load(path):
    d = json.load(open(path, encoding="utf-8"))
    return d, d.get("profile_json"), d.get("judgment")


def _render(d, prof, judgment):
    status = svc.AssessmentStatus(svc.CURRENT, "e", d["client"], d["terminal_am"], generated_at=d.get("generated_at"), profile=prof, judgment=judgment)
    adhoc = presenter.AdhocReport(back_url="/back", json_url="/j", md_url="/m")
    view = presenter.report_view(status, None, "datasphere_core", adhoc)
    return view, templates.get_template(TEMPLATE).render(v=view)


def _main_reading(html):
    """El HTML sin atributos y sin el bloque de detalle técnico (donde vive el glosario): lo que el consultor lee."""
    html = re.sub(r'<div id="asr-evidencia">.*?</div>\s*</section>', "", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return html


needs_corpus = pytest.mark.skipif(not CORPUS, reason="sin Assessments gobernados locales (governed-assessments/ es runtime)")


@needs_corpus
@pytest.mark.parametrize("path", CORPUS, ids=lambda p: Path(p).stem[:48])
def test_real_assessment_reading_is_complete_navigable_and_free_of_engine_codes(path):
    d, prof, judgment = _load(path)
    if not prof:
        pytest.skip("sin perfil")
    before = copy.deepcopy((prof, judgment))
    view, html = _render(d, prof, judgment)
    assert (prof, judgment) == before                                                                        # la presentación no toca el perfil ni el Judgment
    if judgment and judgment.get("status") == "DETERMINED":
        assert view["judgment"]["level"] == judgment["level"]                                                # el nivel mostrado es el persistido
    # guía: cada enlace apunta a un id que existe en ESTE informe
    assert 'id="asr-guia"' in html
    for w in view["reading_guide"]["where"]:
        assert f'id="{w["href"][1:]}"' in html, w
    # ningún código del registro en la lectura principal
    reading = _main_reading(html)
    for code, entry in AL.REGISTRY.items():
        if entry.generic:                                                                                    # UNKNOWN / SYSTEM: palabras que otras familias usan; solo se etiquetan en su contexto
            continue
        assert not re.search(r"\b" + code + r"\b", reading), f"{code} aparece en la lectura principal"
    findings = html[html.index('id="asr-gobierno"'):html.index('id="asr-limitaciones"')] if 'id="asr-gobierno"' in html else html[html.index('id="asr-revision"'):html.index('id="asr-limitaciones"')]
    assert "… (+" not in findings and "…(+" not in findings                                                    # sin recortes opacos en hallazgos (el tope «… (+N)» de un hecho de ÁREA lo graba el motor en el perfil)
    # cada artefacto de cada hallazgo es accesible
    for g in (*view["observations"], *view["review_required"], *view["descriptive"]):
        for a in g["artifacts"]:
            assert a in html
        for o in g["occurrences"]:
            for a in o["artifacts"]:
                assert a in html
    # etiquetas compartidas: nombres técnicos completos
    for g in view["review_required"]:
        if g["id"] == "FIELD_VOCABULARY_MIX":
            raw = next(x for x in prof["review_required"] if x["id"] == "FIELD_VOCABULARY_MIX")
            for o in raw["occurrences"]:
                for name in o["values"].get("technical_names") or []:
                    assert name in html
            assert g["scope_note"] is None or "solo existe el conteo agregado" in g["scope_note"]
    # convención de nombres: las familias muestran su universo completo cuando está atribuido
    nv = NV(prof)
    if nv and nv["all_attributed"]:
        assert sum(f["count"] for f in nv["families"]) == nv["non_compliant"]
        listed = [a for f in nv["families"] for a in f["artifacts"]]
        assert len(listed) == len(set(listed)) == nv["non_compliant"]
        for a in listed:
            assert a in html


@needs_corpus
def test_the_corpus_covers_the_requested_scenarios():
    levels, with_rev, without = set(), 0, 0
    for path in CORPUS:
        d, prof, judgment = _load(path)
        if not prof:
            continue
        levels.add(judgment.get("level") if judgment.get("status") == "DETERMINED" else "ND")
        if prof.get("review_required") or prof.get("observations"):
            with_rev += 1
        else:
            without += 1
    assert {"BUENA", "ADECUADA", "PARCIAL", "ND"} <= levels and with_rev and without


@needs_corpus
def test_the_analisis_vencimiento_acceptance_case():
    path = next(p for p in CORPUS if "AM_ANALISIS_VENCIMIENTO" in p)
    d, prof, judgment = _load(path)
    view, html = _render(d, prof, judgment)
    nv = NV(prof)
    fam = {f["code"]: f for f in nv["families"]}
    assert nv["applicable"] == 28 and nv["non_compliant"] == 24 and nv["compliant"] == 4
    assert len(fam["MISSING_LAYER_PREFIX"]["artifacts"]) == 12 and len(fam["NO_RECOGNIZED_PATTERN"]["artifacts"]) == 12      # los 12 de cada familia, identificados
    assert "Tabla o vista sin prefijo de capa" in html and "Nombre sin patrón reconocido" in html
    assert "VT_MATERIAL" in html and "ZDSP_MATRNMEINS" in html                                                            # (uno de cada familia)
    gov = view["evaluation"]["governance"]["rows"]
    assert [r["finding_type"] for r in gov] == ["NAMING_CONVENTION", "FIELD_VOCABULARY_MIX"] and gov[0]["detail"]["kind"] == "naming"
    guide = {w["section"]: w for w in view["reading_guide"]["where"]}
    assert "no cambia por sí solo el nivel" in guide["Puntos de atención"]["help"] and "no necesariamente cambian el nivel" in html     # tooltip específico + frase visible única
    # Puntos de atención: los artefactos del desvío material ya no se descartan
    assert any(r["artifacts"] for g in view["judgment"]["reasons_grouped"] for r in g["rows"])


# ═══════════════════════════ verificaciones de los documentos de propuesta ═══════════════════════════
def test_the_cta_matrix_lists_exactly_the_32_playbook_keys_and_the_playbook_states_are_documented():
    from tools.catalog.methodological_judgment.cta import PLAYBOOK
    keys = {k for v in PLAYBOOK.values() for k in v[3]}
    assert len(keys) == 32
    doc = (REPO / "docs" / "ASSESSMENT_CTA_MATRIX.md").read_text(encoding="utf-8")
    assert all(f"`{k}`" in doc for k in keys)
    assert {r for r in PLAYBOOK} <= set(re.findall(r"\| ([A-Z]{3}-[A-Z]{3,4}-\d\d) \|", doc))                      # las 15 reglas causa están en la tabla §2


def test_only_material_and_relevant_reasons_decide_the_level_as_the_cta_matrix_states():
    from tools.catalog.methodological_judgment import contract as C
    from tools.catalog.methodological_judgment import engine as E
    def reason(cls, state=C.OPEN_CANDIDATE):
        return {"classification": cls, "state": state, "reason_id": cls, "rule_id": "X"}
    strengths = [{"rule_id": rid} for ids in E._STRENGTH_BY_DIM.values() for rid in ids[:1]]
    assert E._decide([reason(C.MATERIAL_DEVIATION)], [], [])[1] == C.PARCIAL
    assert E._decide([reason(C.RELEVANT_WEAKNESS)], [], [])[1] == C.ADECUADA
    assert E._decide([reason(C.LOCAL_WEAKNESS)], [], [])[0] == C.NOT_DETERMINABLE                              # LOCAL no decide nada (sin fortalezas: LVL-06)
    assert E._decide([reason(C.LOCAL_WEAKNESS)], strengths, [])[1] == C.BUENA                                  # ...ni impide BUENA
    assert E._decide([reason(C.MATERIAL_DEVIATION, C.GOVERNED_EXCEPTION)], [], [])[1] is None                  # la excepción gobernada retira la razón
    assert E._decide([reason(C.MATERIAL_DEVIATION, C.ACCEPTED_AS_DESIGNED)], [], [])[1] == C.PARCIAL          # «aceptado como diseñado» NO la retira
    assert set(C.REMOVING_STATES) == {C.GOVERNED_EXCEPTION, C.NOT_APPLICABLE}


def test_source_state_definitions_proposed_for_the_pending_vocabulary_match_the_engine():
    from tools.catalog.modeling_assessment.facts_source import source_state_of
    got = {e: source_state_of(e).value for e in ("ABAP_CDS.X", "SAPI.0MATERIAL", "ABAPTABLES.MARC", "ABAPTABLES.NSDM_V_MKPF", "CONNX.BWBRIDGE.T", "SQLSERVER.T", "")}
    assert got == {"ABAP_CDS.X": "CDS_VIEW", "SAPI.0MATERIAL": "SAPI_ODP", "ABAPTABLES.MARC": "RAW_PHYSICAL_TABLE", "ABAPTABLES.NSDM_V_MKPF": "STANDARD_VIEW_ON_PHYSICAL_TABLE",
                   "CONNX.BWBRIDGE.T": "BW_BRIDGE", "SQLSERVER.T": "OTHER_ORIGIN", "": "NO_REMOTE_ORIGIN"}
    assert "ya no" not in AL.REGISTRY and "CDS_VIEW" not in AL.REGISTRY and "OTHER_ORIGIN" not in AL.REGISTRY      # propuestas SIN registrar hasta aceptación del owner


# ═══════════════════════════ sin afirmaciones de exhaustividad sobre agregados ═══════════════════════════
def test_a_partially_attributed_naming_view_never_claims_to_list_everything():
    from webapp.services import asset_evaluation as AE
    doc = _doc([{"identity": "A", "non_compliant_artifacts": ["x1", "x2", "y1"],
                 "violation_family_histogram": [{"key": "MISSING_LAYER_PREFIX", "count": 2}, {"key": "NO_RECOGNIZED_PATTERN", "count": 1}], "distinct_violation_families": 2}],
               [{"key": "MISSING_LAYER_PREFIX", "count": 2}, {"key": "NO_RECOGNIZED_PATTERN", "count": 1}], ["x1", "x2", "y1"])
    nv = NV(doc)
    s28 = doc["canonical_evidence"]["naming_evidence"]["hygiene"]["artifact_name_populations"]["section_28"]
    row = AE.governed_findings([], s28, None, nv)[0]
    assert "por familia" not in row["link"]["text"] and "familia" not in row["evidence"].split(":")[-1]          # ni el enlace ni el texto prometen el reparto por familia
    html = templates.get_template("partials/assessment_report_macros.html").module.naming_families(nv, 4)
    assert html.count("solo el conteo agregado") == 2 and "no permite identificar cuáles" in html
    assert "Sin atribución por familia" in html and "x1" in html and "y1" in html                               # los artefactos se listan, sin repartirlos


# ═══════════════════════════ frontera del paquete del motor (RI2 / RI2b) ═══════════════════════════
def test_the_labels_registry_does_not_import_the_engine_package_and_the_classifier_comes_through_the_public_api():
    import ast
    tree = ast.parse((REPO / "webapp" / "services" / "assessment_labels.py").read_text(encoding="utf-8"))
    imported = [n.module if isinstance(n, ast.ImportFrom) else a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (getattr(n, "names", None) or [None])]
    assert not any("modeling_assessment" in str(m) or "tools" in str(m).split(".")[:1] for m in imported), imported       # solo el presenter/servicio consumen el paquete
    from tools.catalog.modeling_assessment import naming_rules as NR, presentation as P
    assert "naming_violation_family" in P.__all__
    for text in ("tabla/vista sin prefijo de capa (§28: <Capa><Artefacto><Uso>_)", "no sigue §28 <Capa><Artefacto><Uso>_ ni un artefacto homologado (DF/RF/FT/AM/…)", "motivo desconocido"):
        assert P.naming_violation_family(text) == NR.violation_family(text).value                                      # delega, no duplica la regla
    assert P.naming_violation_family("motivo desconocido") == "UNMAPPED_ISSUE"
