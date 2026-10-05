"""M7 — Markdown (`render_md.py`): se deriva del MISMO documento que el JSON; en ESPANOL; agrupado, determinista, ejes separados, sin falsas ausencias ni scores."""
from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

import pytest

from tests._modeling_assessment_m7_fixtures import scenario_pools
from tests.test_modeling_assessment_assessability import _local_chain
from tests.test_modeling_assessment_profile import _groups, _no_terminal_scope_doc, build
from tools.catalog.modeling_assessment import profile as P
from tools.catalog.modeling_assessment import render_md as R

PKG = Path(__file__).resolve().parent.parent / "tools" / "catalog" / "modeling_assessment"

BANNED = re.compile(r"\b(score|scores|scoring|band|bands|weight|weights|penalty|penalties|rating|ranking|rank|PLENO|BUENO|PARCIAL|grade|grades|maturity|"
                    r"puntaje|puntajes|puntuaci[oó]n|banda|bandas|penalizaci[oó]n|penalidad|ponderaci[oó]n|madurez)\b|\b[1-5]\s*/\s*5\b", re.I)


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    out = [(am, build(ents, am, ev)[0]) for ents, am, ev in scenario_pools(tmp_path_factory.mktemp("m7md"))]
    out.append(("AM_C", build(_local_chain(), "AM_C")[0]))
    return out


def _occ(n):
    return f"{n} ocurrencia{'s' if n != 1 else ''}"


def _title(g):
    return g["title"].upper() if g["kind"] == "OBSERVATION" else g["title"]


def test_markdown_is_deterministic_and_has_the_approved_spanish_structure(docs):
    expected = ["# Autoevaluación de Modelado", "## Resumen", "## Arquitectura / Modelado", "## Convención de nombres", "## Fuente / Extracción",
                "## Observaciones", "## Casos a revisar", "## No evaluado / Limitaciones de evidencia", "## Evidencia / Detalle técnico"]
    for am, doc in docs:
        a, b = R.render_markdown(doc), R.render_markdown(copy.deepcopy(doc))
        assert a == b and a.endswith("\n") and not a.endswith("\n\n")
        heads = [ln for ln in a.splitlines() if ln.startswith("# ") or ln.startswith("## ")]
        assert heads == expected, (am, heads)
        assert "- **Cliente:** Acme" in a and f"- **Modelo analítico terminal:** {am}" in a
        arch = a[a.index("## Arquitectura / Modelado"):a.index("## Convención de nombres")]
        assert re.findall(r"^### (.+)$", arch, re.M) == ["Modelo analítico", "Propagación", "Armonización", "Ingesta"]
    # RESUMEN -> AREAS -> HALLAZGOS -> EVIDENCIA: el resumen precede a todo lo demas y no hay hechos crudos antes de los hallazgos
    md = R.render_markdown(docs[0][1])
    assert md.index("## Resumen") < md.index("## Arquitectura / Modelado") < md.index("## Observaciones") < md.index("## Evidencia / Detalle técnico")
    assert "@" not in re.sub(r"`[^`]*`", "", md)


def test_repeated_findings_are_grouped_with_their_occurrence_count(docs):
    checked = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for g in _groups(doc):
            assert f"{_title(g)} — {_occ(g['occurrence_count'])}" in md, g["id"]
            assert md.count(f"`{g['id']}`") >= 1
            checked += 1
            if g["occurrence_count"] > R.MAX_OCCURRENCES_SHOWN:
                rest = g["occurrence_count"] - R.MAX_OCCURRENCES_SHOWN
                assert f"… y {_occ(rest)} más" in md
    assert checked >= 20


def _group_block_text(md, g):
    heading = re.search(rf"^#{{3,4}} {re.escape(_title(g))} — {_occ(g['occurrence_count'])}$", md, re.M)
    assert heading, g["id"]
    start = heading.start()
    nxt = [i for i in (md.find("\n### ", start + 1), md.find("\n#### ", start + 1), md.find("\n## ", start + 1)) if i != -1]
    return md[start:min(nxt) if nxt else len(md)]


def test_occurrence_unit_and_affected_artifacts_are_stated_separately(docs):
    differing = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for g in _groups(doc):
            block = _group_block_text(md, g)
            n, m = g["occurrence_count"], len(g["affected_artifacts"])
            assert f"- **Ocurrencias:** {n} (unidad de cada ocurrencia: {g['occurrence_unit']})" in block
            line = next(ln for ln in block.splitlines() if ln.startswith("- **Artefactos involucrados"))
            assert line.startswith(f"- **Artefactos involucrados ({m}):**")
            if m != n:
                differing += 1
                art = f"{m} artefacto{'s' if m != 1 else ''} distinto{'s' if m != 1 else ''}"
                expected = f"(la ocurrencia involucra {art})" if n == 1 else f"(las {n} ocurrencias involucran {art})"
                assert line.endswith(expected), (g["id"], line[-90:])
            else:
                assert "involucra" not in line.split(":**", 1)[1]
    assert differing                                                                  # p. ej. relaciones fuente→consumidor: 7 ocurrencias, 14 artefactos


def test_each_occurrence_line_uses_only_public_values_and_is_capped(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for g in _groups(doc):
            for o in g["occurrences"][:R.MAX_OCCURRENCES_SHOWN]:
                line = R._occurrence_line(g["id"], o)
                assert line and f"  - {line}" in md
            for o in g["occurrences"][R.MAX_OCCURRENCES_SHOWN:]:
                assert f"  - {R._occurrence_line(g['id'], o)}\n" not in md
    assert R.MAX_OCCURRENCES_SHOWN == 8


def test_every_signal_has_an_explicit_occurrence_formatter():
    assert set(R._FMT) - set(__import__("tools.catalog.modeling_assessment.model", fromlist=["x"]).LEGACY_SIGNAL_ALIASES) == set(P.DISPLAY)          # los alias historicos solo se LEEN


def test_summary_parity_with_the_json_document(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        s = doc["summary"]
        assert f"**Cobertura:** estricta {R._pct(s['coverage']['strict'])} · inclusiva {R._pct(s['coverage']['inclusive'])}" in md
        assert f"**Confianza:** {R._LEVEL_TEXT[s['confidence']['level']]}" in md
        ctx = s["context_required"]
        n, k = ctx["open_questions"], ctx["questions_backed_by_findings"]
        assert f"**Preguntas abiertas:** {n} pregunta{'s' if n != 1 else ''}" in md
        assert f"{k} {'está relacionada' if k == 1 else 'están relacionadas'} con hallazgos detectados y {ctx['questions_without_findings']} " in md
        assert "sin hallazgo asociado. Una pregunta puede estar vinculada a varias ocurrencias." in md
        for q in ctx["questions"]:
            assert q["question"] in md
        og, rg = len(doc["observations"]), len(doc["review_required"])
        on, rn = sum(g["occurrence_count"] for g in doc["observations"]), sum(g["occurrence_count"] for g in doc["review_required"])
        assert f"- **Casos a revisar:** {rn} caso{'s' if rn != 1 else ''}" in md                                                           # unidad: OCURRENCIAS de revisión
        assert f"agrupados en {rg} tipo{'s' if rg != 1 else ''} de revisión" in md or rn == 0
        assert f"- **Observaciones:** {og} grupo{'s' if og != 1 else ''} de observación ({_occ(on)})" in md
        for a in doc["areas"]:
            c = a["required_surfaces"]
            assert (f"| {R._MACRO_TEXT[a['macro_dimension']]} | {R._AREA_TITLE[a['id']]} | {R._STATE_TEXT[a['assessability']]} | "
                    f"{c['assessed']} / {c['partially_assessed']} / {c['not_assessed']} |") in md
            assert f"| {R._AREA_TITLE[a['id']]} | {c['assessed']} | {c['partially_assessed']} | {c['not_assessed']} | {c['not_applicable']} |" in md
        for r in doc["rule_results"]:
            assert f"| {r['title']} (`{r['rule']}`) | {R._RULE_RESULT_TEXT[r['result']]} | {r['finding_count']} |" in md


def test_context_units_are_stated_without_mixing_questions_and_occurrences(docs):
    for _am, doc in docs:
        ctx = doc["summary"]["context_required"]
        backed = [q for q in ctx["questions"] if q["occurrence_count"] is not None]
        assert ctx["questions_backed_by_findings"] == len(backed) and ctx["questions_without_findings"] == ctx["open_questions"] - len(backed)
        assert ctx["occurrences"] == sum(g["occurrence_count"] for g in (*doc["observations"], *doc["review_required"]))
    md = R.render_markdown(docs[0][1])
    head = next(ln for ln in md.splitlines() if ln.startswith("- **Preguntas abiertas:**"))
    detail = md.splitlines()[md.splitlines().index(head) + 1]
    assert "pregunta" in head and "ocurrencia" not in head                                                    # el titular cuenta TIPOS de pregunta
    assert "relacionadas con hallazgos detectados" in detail or "relacionada con hallazgos detectados" in detail
    assert "vinculada a varias ocurrencias" in detail


def test_each_area_shows_its_real_composed_state_not_a_generic_one(docs):
    states = set()
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for a in doc["areas"]:
            states.add(a["assessability"])
            assert f"| {R._AREA_TITLE[a['id']]} | {R._STATE_TEXT[a['assessability']]} |" in md
            sec = md[md.index(f"### {R._AREA_TITLE[a['id']]}") if f"### {R._AREA_TITLE[a['id']]}" in md else md.index(f"## {R._AREA_TITLE[a['id']]}"):]
            assert f"**Estado de la evaluación:** {R._STATE_TEXT[a['assessability']]}" in sec
        assert "con evidencia evaluada |" not in md                                    # la tabla ya no esconde diferencias bajo una etiqueta generica
    assert {"ASSESSED", "PARTIALLY_ASSESSED"} <= states


def test_markdown_changes_only_when_the_document_changes(docs):
    doc = docs[0][1]
    base = R.render_markdown(doc)
    mutated = copy.deepcopy(doc)
    mutated["summary"]["coverage"]["strict"] = 0.1234
    assert R.render_markdown(mutated) != base and "12.3%" in R.render_markdown(mutated)
    mutated = copy.deepcopy(doc)
    _groups(mutated)[0]["occurrence_count"] += 5
    assert R.render_markdown(mutated) != base


def test_findings_summary_line_pluralises_correctly_for_zero_one_and_many(docs):
    doc = copy.deepcopy(docs[0][1])
    g = copy.deepcopy(doc["review_required"][0])
    g["occurrences"], g["occurrence_count"] = g["occurrences"][:1], 1
    doc["observations"], doc["review_required"], doc["descriptive_signals"] = [], [g], []
    for area in doc["areas"]:
        area["finding_groups"] = [x for x in area["finding_groups"] if x == g["id"]]
    md0 = R.render_markdown(doc)
    assert "- **Observaciones:** 0 grupos de observación (0 ocurrencias)" in md0 and "- **Casos a revisar:** 1 caso" in md0 and "agrupados en 1 tipo de revisión" in md0
    doc["observations"] = [copy.deepcopy(g)]
    md1 = R.render_markdown(doc)
    assert "- **Observaciones:** 1 grupo de observación (1 ocurrencia)" in md1 and "- **Casos a revisar:** 1 caso" in md1


def test_renderer_is_a_pure_function_of_the_document():
    tree = ast.parse((PKG / "render_md.py").read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(("." * n.level) + (n.module or ""))
    assert mods <= {"__future__", "typing"}, mods                              # no importa modelo, hechos, senales ni legacy: solo formatea el documento
    text = (PKG / "render_md.py").read_text(encoding="utf-8")
    for needle in ("open(", "read_text", "write_text", "datetime", "time.", "random"):
        assert needle not in text, needle


# ── ejes separados ───────────────────────────────────────────────────────────
def test_coverage_confidence_and_context_appear_as_separate_summary_items(docs):
    for _am, doc in docs:
        summary = R.render_markdown(doc).split("## Arquitectura / Modelado")[0]
        labels = [b.split(":**")[0] for b in summary.splitlines() if b.startswith("- **")]
        axes = ("- **Cobertura", "- **Confianza", "- **Casos a revisar", "- **Preguntas abiertas")
        assert [x for x in labels if x in axes] == list(axes)                                            # cuatro ejes separados, cada uno con su unidad
        cov_block = summary[summary.index("- **Cobertura"):summary.index("- **Confianza")]
        conf_block = summary[summary.index("- **Confianza"):summary.index("- **Casos a revisar")]
        review_block = summary[summary.index("- **Casos a revisar"):summary.index("- **Preguntas abiertas")]
        assert "pregunta" not in review_block.lower() and "caso" in review_block                          # casos: ocurrencias de revisión, no preguntas
        assert "pregunta" not in cov_block.lower()
        assert "contexto" not in conf_block.lower().replace("preguntas de contexto aún abiertas", "")
        assert "estricta" not in conf_block and "%" not in conf_block                  # nada de matematica de cortes provisorios en el resumen
        assert "No es una afirmación sobre la calidad del modelo" in conf_block


def test_confidence_math_is_in_the_technical_section_not_the_summary(docs):
    md = R.render_markdown(docs[0][1])
    assert "### Componentes de confianza" in md and md.index("### Componentes de confianza") > md.index("## No evaluado / Limitaciones de evidencia")
    assert "La cobertura, las preguntas de contexto abiertas y la evidencia del plano de Procesamiento NO son insumos de la confianza." in md


# ── ausencia: nunca "sin problema" cuando no esta establecida ────────────────
def test_absence_not_established_is_never_written_as_no_issue(docs):
    seen_not_established = seen_assessed = 0
    marks = ("ausencia evaluada", "NO ESTABLECIDO", "patrón detectado", "no aplica")
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for r in doc["rule_results"]:
            lines = [ln for ln in md.splitlines() if r["title"] in ln and any(m in ln for m in marks)]
            assert lines
            for ln in lines:
                if r["result"] == "ABSENCE_NOT_ESTABLISHED":
                    seen_not_established += 1
                    assert "NO ESTABLECIDO" in ln and "no puede afirmarse la ausencia" in ln and "patrón no detectado" not in ln
                elif r["result"] == "NOT_APPLICABLE":
                    assert "no aplica" in ln and "patrón no detectado" not in ln
                elif r["result"] == "ASSESSED_ABSENCE":
                    seen_assessed += 1
                    assert "ausencia evaluada (patrón no detectado)" in ln and "NO ESTABLECIDO" not in ln
        banned = re.compile(r"\b(sin problemas?|sin incidencias|todo bien|nada que reportar|no hay problemas?|no issues?|no problems?|all good)\b", re.I)
        assert not banned.search(md)
        if any(r["result"] == "ABSENCE_NOT_ESTABLISHED" for r in doc["rule_results"]):
            assert "la ausencia de hallazgos NO está establecida" in md
    assert seen_not_established and seen_assessed


def test_empty_observation_and_review_sections_do_not_claim_absence_of_issues():
    doc = build(_local_chain(), "AM_C")[0]
    assert not doc["observations"]
    md = R.render_markdown(doc)
    sec = md[md.index("## Observaciones"):md.index("## Casos a revisar")]
    assert "evidencia que pudo evaluarse" in sec and "*No evaluado / Limitaciones de evidencia*" in sec and "sin problema" not in sec.lower()


def test_a_completely_not_assessed_dimension_is_explicit_in_the_summary():
    md = R.render_markdown(build(_local_chain(), "AM_C")[0])
    assert "**Dimensión Fuente / Extracción:** SIN EVIDENCIA EVALUADA — ninguna evidencia requerida pudo evaluarse." in md
    assert "| Fuente / Extracción | Fuente / Extracción | No evaluado |" in md
    assert "| Arquitectura / Modelado | Modelo analítico | Evaluado |" in md
    src = md[md.index("## Fuente / Extracción"):md.index("## Observaciones")]
    assert "No evaluado" in src


def test_evidence_limitations_table_lists_reasons_with_public_codes_and_coverage_role(docs):
    md = R.render_markdown(docs[0][1])
    sec = md[md.index("## No evaluado / Limitaciones de evidencia"):md.index("## Evidencia / Detalle técnico")]
    rows = [ln for ln in sec.splitlines() if ln.startswith("| ") and "(`" in ln and "---" not in ln and not ln.startswith("| Evidencia |")
            and not ln.startswith("| Componente")]
    assert rows and all(re.search(r"\(`[A-Z_]+`\) \| (cuenta|no cuenta) \|$", r) for r in rows)
    for r in rows:
        assert re.search(r"\(`([A-Z_]+)`\)", r).group(1) in P.PUBLIC_REASONS


def test_findings_whose_rule_inputs_were_incomplete_state_the_evidence_limit(docs):
    limited = full = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for g in _groups(doc):
            heading = re.search(rf"^#{{3,4}} {re.escape(_title(g))} — {_occ(g['occurrence_count'])}$", md, re.M)
            assert heading, g["id"]
            start = heading.start()
            nxt = [i for i in (md.find("\n### ", start + 1), md.find("\n#### ", start + 1), md.find("\n## ", start + 1)) if i != -1]
            block = md[start:min(nxt) if nxt else len(md)]
            st = g.get("evidence_state")
            if st and st["assessability"] != "ASSESSED":
                limited += 1
                assert "**Límite de evidencia:**" in block and st["reason"]["reason_summary"] in block
                assert f"los insumos de la regla están {R._STATE_PLURAL_TEXT[st['assessability']]} (" in block
            else:
                full += 1
                assert "**Límite de evidencia:**" not in block
    assert limited and full


def test_findings_are_presented_as_context_required_with_questions_and_no_judgment(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for g in (*doc["observations"], *doc["review_required"]):
            assert "**Estado:** abierto — requiere contexto (no se emite juicio)" in md
            assert f"**Pregunta para el proyecto:** {g['context_required']['question']}" in md
            assert "**Cómo NO leerlo:**" in md
        for g in doc["descriptive_signals"]:
            assert g["title"] in md


def test_each_group_states_why_it_is_shown_and_limitations_name_the_affected_units(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for g in _groups(doc):
            assert g["why_shown"] and f"- **Por qué se muestra:** {g['why_shown']}" in md
        for lim in doc["evidence_limitations"]:
            assert f"| {R._cell(lim['title'])} (`{lim['surface_id']}`) |" in md
            if lim["affected_units"]:
                assert f"- {lim['title']}: {R._units(lim['affected_units'], 8)}" in md
        if any(lim["affected_units"] for lim in doc["evidence_limitations"]):
            assert "Evidencia no disponible o dependiente de contexto (unidades afectadas):" in md
        assert "Where the evidence is missing" not in md
        for a in doc["areas"]:
            if a["assessability"] not in ("ASSESSED", "NOT_APPLICABLE"):
                assert "ver *No evaluado / Limitaciones de evidencia*" in md


def test_boundary_artifacts_are_shown_with_a_breakdown_that_reconciles_to_the_total(docs):
    seen = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for a in doc["areas"]:
            for h in a["highlights"]:
                if "breakdown" in h:
                    seen += 1
                    assert f"- {h['label']}: {h['value']}" in md
                    for b in h["breakdown"]:
                        assert f"  - {b['name']}: {b['count']}" in md
    assert seen >= 5


# ── vocabulario ──────────────────────────────────────────────────────────────
def test_markdown_has_no_score_band_or_ranking_vocabulary_in_either_language(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        assert "> Perfil de evidencia, cobertura y confianza — Fase 1 / validación en paralelo." in md        # encabezado en positivo
        assert not BANNED.search(md), BANNED.search(md).group(0)                                              # sin excepciones: ni siquiera como negacion


# ── idioma: la presentacion publica es espanol ───────────────────────────────
ENGLISH_PRESENTATION_TERMS = ("Assessment state", "Assessment Scope", "What was observed", "Review Required", "Not Assessed", "Evidence Limitations",
                              "Why it is shown", "How NOT to read it", "Context required", "Affected artifacts", "Summary", "Findings", "Observations",
                              "Architecture / Modeling", "Source / Extraction", "Naming", "Where the evidence", "Rule results", "Confidence components",
                              "Question for the project", "Evidence limit", "Technical Detail", "Phase 1", "no score", "no band", "shadow")


def test_public_markdown_is_spanish_and_has_no_known_english_presentation_terms(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        prose = re.sub(r"`[^`]*`", "", md)                                              # los identificadores tecnicos pueden seguir en ingles
        stale = [t for t in ENGLISH_PRESENTATION_TERMS if t in prose]
        assert stale == [], stale
    md = R.render_markdown(docs[0][1])
    for expected in ("Autoevaluación de Modelado", "**Cobertura:**", "**Confianza:**", "**Preguntas abiertas:**", "Qué se observó", "**Por qué se muestra:**",
                     "**Cómo NO leerlo:**", "**Pregunta para el proyecto:**", "Parcialmente evaluado", "Componentes de confianza", "Cobertura por área"):
        assert expected in md, expected


def test_status_labels_come_from_one_centralised_spanish_mapping():
    assert R._STATE_TEXT == {"ASSESSED": "Evaluado", "PARTIALLY_ASSESSED": "Parcialmente evaluado", "NOT_ASSESSED": "No evaluado",
                             "NOT_APPLICABLE": "No aplica", "INDETERMINATE": "Indeterminado"}
    assert R._RULE_RESULT_TEXT["PATTERN_FOUND"] == "patrón detectado" and R._RULE_RESULT_TEXT["ASSESSED_ABSENCE"].startswith("ausencia evaluada")
    assert R._RULE_RESULT_TEXT["ABSENCE_NOT_ESTABLISHED"].startswith("NO ESTABLECIDO")
    assert set(R._STATE_TEXT) >= {"ASSESSED", "PARTIALLY_ASSESSED", "NOT_ASSESSED", "NOT_APPLICABLE", "INDETERMINATE"}
    assert set(R._AREA_TITLE) == {a for a in P.M.MACRO_DIMENSION_OF_AREA.__class__ and [k.value for k in P.M.MACRO_DIMENSION_OF_AREA]}
    tree = ast.parse((PKG / "render_md.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_markdown")
    literals = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert not [x for x in literals if x in ("Evaluado", "No evaluado", "Parcialmente evaluado", "No aplica")]      # el renderer no re-traduce estados a mano


# ── NOT_APPLICABLE / INDETERMINATE ───────────────────────────────────────────
def test_not_applicable_markdown_says_there_is_no_assessment_and_reports_nothing_else():
    md = R.render_markdown(_no_terminal_scope_doc())
    assert "**Este cliente no tiene evaluación**" in md and "`NO_TERMINAL_ANALYTIC_MODEL`" in md
    for forbidden in ("**Cobertura:**", "**Confianza:**", "## Observaciones", "## Casos a revisar"):
        assert forbidden not in md
    assert "- **Modelo analítico terminal:** —" in md and "no aplica" in md
    assert "El cliente no tiene un modelo analítico terminal" in md


def test_indeterminate_markdown_reports_the_load_error_publicly():
    doc = P.build_indeterminate_assessment(client="Clash", requested_model="4AM_X", message="colisión de entidades en el conjunto de artefactos", pool_signature="abc")
    md = R.render_markdown(doc)
    assert "`POOL_LOAD_ERROR`" in md and "colisión de entidades en el conjunto de artefactos" in md and "**Cobertura:**" not in md and "Indeterminado" in md


def test_the_public_json_is_the_semantic_source_of_truth_of_every_displayed_section(docs):
    for _am, doc in docs:
        assert R.render_markdown(json.loads(P.canonical_assessment_json(doc))) == R.render_markdown(doc)          # ida y vuelta por el JSON publicado
    doc = copy.deepcopy(docs[0][1])
    sentinel = "ZZ-SENTINEL"
    doc["scope"]["client"] = f"{sentinel}-client"
    doc["summary"]["confidence"]["main_limitation"] = {"component": "x", "summary": f"{sentinel}-limitation"}
    doc["summary"]["context_required"]["questions"][0]["question"] = f"{sentinel}-question?"
    area = next(a for a in doc["areas"] if a["id"] == "NAMING")
    area["highlights"].append({"id": "s", "label": f"{sentinel}-highlight", "value": 7})
    area["highlights"].append({"id": "b", "label": f"{sentinel}-total", "value": 5, "breakdown": [{"name": f"{sentinel}-part", "count": 5}]})
    g = doc["review_required"][0]
    g["title"], g["why_shown"], g["occurrences"][0]["values"]["__x"] = f"{sentinel}-title", f"{sentinel}-why", 1
    g["affected_artifacts"] = [f"{sentinel}-artifact"]
    g["occurrence_unit"] = f"{sentinel}-unit"
    doc["evidence_limitations"][0]["reason"]["reason_summary"] = f"{sentinel}-reason"
    doc["rule_results"][0]["title"] = f"{sentinel}-rule"
    md = R.render_markdown(doc)
    for part in ("client", "limitation", "question?", "highlight", "total", "part", "title", "why", "artifact", "unit", "reason", "rule"):
        assert f"{sentinel}-{part}" in md, part


def test_closure_level_units_are_shown_in_spanish_not_as_the_internal_id(docs):
    seen = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for lim in doc["evidence_limitations"]:
            if "closure" in lim["affected_units"]:
                seen += 1
                line = next(ln for ln in md.splitlines() if ln.startswith(f"- {lim['title']}: "))
                assert "cierre semántico completo" in line and "`closure`" not in line
    assert seen


# ═══ M8 calibracion 1: contratos de presentacion de las decisiones del owner ═══
def test_the_top_level_state_describes_execution_not_evidence_assessability(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        header = md[:md.index("## Resumen")]
        assert "- **Estado del assessment:** Ejecutado" in header and "Estado de la evaluación" not in header      # el encabezado ya no sugiere evidencia completa
        assert "**Estado de la evaluación:** " in md                                                              # la evaluabilidad sigue siendo POR AREA
    md = R.render_markdown(build(_local_chain(), "AM_C")[0])                         # la Fuente NO se pudo evaluar, pero la ejecucion fue completa
    assert "- **Estado del assessment:** Ejecutado" in md and "| Fuente / Extracción | Fuente / Extracción | No evaluado |" in md
    assert "- **Estado del assessment:** No aplica" in R.render_markdown(_no_terminal_scope_doc())
    indet = P.build_indeterminate_assessment(client="Clash", requested_model="4AM_X", message="colisión", pool_signature="abc")
    assert "- **Estado del assessment:** Indeterminado" in R.render_markdown(indet)
    assert R._EXECUTION_TEXT == {"ASSESSED": "Ejecutado", "NOT_APPLICABLE": "No aplica", "INDETERMINATE": "Indeterminado"}


def test_not_applicable_rows_are_absent_from_the_limitations_section_and_listed_in_the_technical_detail(docs):
    seen = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        lim = md[md.index("## No evaluado / Limitaciones de evidencia"):md.index("## Evidencia / Detalle técnico")]
        assert "| No aplica |" not in lim and "no aplica" not in lim.lower().replace("¿cuenta para la cobertura?", "")
        assert all(R._STATE_TEXT[x["assessability"]] in ("No evaluado", "Parcialmente evaluado", "Indeterminado") for x in doc["evidence_limitations"])
        tech = md[md.index("## Evidencia / Detalle técnico"):]
        if doc["not_applicable"]:
            seen += 1
            assert "### Evidencia que no aplica (trazabilidad; no son limitaciones de evidencia)" in tech
            for s in doc["not_applicable"]:
                assert f"| {R._cell(s['title'])} (`{s['surface_id']}`) |" in tech
        else:
            assert "### Evidencia que no aplica" not in tech
    assert seen


def test_context_questions_deduplicated_by_semantic_key_appear_once_and_are_traceable(docs):
    seen = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        ctx = doc["summary"]["context_required"]
        for d in ctx["deduplicated_questions"]:
            seen += 1
            assert P.CONTEXT_QUESTION_TEXT[d["id"]] not in md                                  # no se repite la pregunta generica
            assert f"- `{d['id']}` → ya la formula el grupo `{d['covered_by']}`" in md          # trazabilidad en el detalle tecnico
            assert next(g for g in _groups(doc) if g["id"] == d["covered_by"])["context_required"]["question"] in md
        for q in ctx["questions"]:
            assert md.count(q["question"]) >= 1
        if ctx["deduplicated_questions"]:
            assert "### Preguntas de contexto no repetidas (ya formuladas por un hallazgo)" in md
        else:
            assert "### Preguntas de contexto no repetidas" not in md
    assert seen


def test_findings_are_rendered_in_the_document_priority_order(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        for kind, section_start, section_end in (("review_required", "## Casos a revisar", "## No evaluado / Limitaciones de evidencia"),
                                                 ("observations", "## Observaciones", "## Casos a revisar")):
            sec = md[md.index(section_start):md.index(section_end)]
            headings = re.findall(r"^### (.+?) — \d+ ocurrencias?$", sec, re.M)
            expected = [_title(g) for g in doc[kind]]
            assert headings == expected, (kind, headings, expected)


def test_direct_raw_lines_show_distance_and_dependents_when_available_and_say_so_when_not(docs):
    seen_defined = 0
    for _am, doc in docs:
        md = R.render_markdown(doc)
        g = next((x for x in doc["observations"] if x["id"] == "DIRECT_RAW_TABLE_ACCESS"), None)
        for o in (g["occurrences"][:R.MAX_OCCURRENCES_SHOWN] if g else []):
            line = R._occurrence_line("DIRECT_RAW_TABLE_ACCESS", o)
            v = o["values"]
            assert f"artefactos dependientes aguas abajo: {v['transitive_lineage_dependents']}" in line and f"  - {line}" in md
            if v["lineage_distance"] is not None:
                seen_defined += 1
                assert f"distancia al modelo analítico: {v['lineage_distance']}" in line
            assert "severidad" not in line.lower() and "prioridad" not in line.lower()
    doc = copy.deepcopy(next(d for _a, d in docs if any(x["id"] == "DIRECT_RAW_TABLE_ACCESS" for x in d["observations"])))
    g = next(x for x in doc["observations"] if x["id"] == "DIRECT_RAW_TABLE_ACCESS")
    g["occurrences"][0]["values"]["lineage_distance"] = None                                  # fuente que no alcanza al AM por linaje estricto: distancia no definida
    line = R._occurrence_line("DIRECT_RAW_TABLE_ACCESS", g["occurrences"][0])
    assert "distancia al modelo analítico: no definida" in line and f"  - {line}" in R.render_markdown(doc)
    assert seen_defined


def test_closure_composition_and_numeric_non_measure_evidence_are_rendered_in_the_analytic_model_section(docs):
    for _am, doc in docs:
        md = R.render_markdown(doc)
        am_sec = md[md.index("### Modelo analítico"):md.index("### Propagación")]
        assert "- Cierre semántico del modelo analítico (artefactos): " in am_sec
        row = next(h for a in doc["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"] if h["id"] == "semantic_closure")
        for b in row["breakdown"]:
            assert f"  - {b['name']}: {b['count']}" in am_sec
        assert "- Atributos numéricos expuestos que no están declarados como medida: " in am_sec
