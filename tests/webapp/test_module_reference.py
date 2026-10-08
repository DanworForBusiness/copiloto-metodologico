"""
test_module_reference.py — las referencias a secciones de módulos son SEMÁNTICAS (módulo + clave de sección), jamás un número ordinal.

El número (§29, §30…) se resuelve al PRESENTAR contra la estructura vigente del módulo (`content_service.load_module`); si se inserta una sección antes,
el mismo hallazgo gobernado pasa a mostrar el número nuevo. Los textos históricos con ordinales viejos se traducen al vuelo (sin migrar archivos).
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from webapp.services import assessment_presenter as presenter
from webapp.services import asset_evaluation as ae
from webapp.services import content_service
from webapp.services import module_reference as mr

REPO = Path(__file__).resolve().parents[2]
STEM = "04_Modelado_y_Desarrollo"
LEGACY_S28 = {"applicable": 4, "non_compliant": 1, "non_compliant_artifacts": ["4AM_X"],
              "violation_reason_histogram": [{"key": "artefacto tipado 'AM' no debe llevar prefijo de capa '4' (§29: los artefactos tipados se nombran sin número de capa)", "count": 1}]}


def _naming_number():
    doc = content_service.load_module(STEM, "datasphere_core")
    return next(s.number for s in doc.sections if s.title == "Convención de Nombres")


def _renumbered_with_a_section_inserted_before_naming(monkeypatch):
    """Registro sintético: se inserta una sección ANTES de «Convención de Nombres» (los números siguientes suben 1)."""
    real = content_service.load_module
    doc = real(STEM, "datasphere_core")
    idx = next(i for i, s in enumerate(doc.sections) if s.title == "Convención de Nombres")
    shifted = tuple(dataclasses.replace(s, number=s.number + 1) if i >= idx else s for i, s in enumerate(doc.sections))
    inserted = content_service.ModuleSection(doc.sections[idx].number, "Sección nueva en el medio", "seccion-nueva", "", "")
    new_doc = dataclasses.replace(doc, sections=shifted[:idx] + (inserted,) + shifted[idx:])
    monkeypatch.setattr(content_service, "load_module", lambda stem, pack="datasphere_core": new_doc if stem == STEM else real(stem, pack))


def _finding():
    rows = ae.governed_findings([], {**LEGACY_S28, "violation_reason_histogram": [{**r, "key": mr.strip_ordinals(r["key"])} for r in LEGACY_S28["violation_reason_histogram"]]},
                                mr.resolve_ref(mr.NAMING_CONVENTION))
    return rows[0]


# A. identidad semántica + resolución contra el registro vigente
def test_the_naming_finding_resolves_its_reference_from_the_current_module_registry():
    n = _naming_number()
    ref = mr.resolve_ref(mr.NAMING_CONVENTION)
    assert (ref["module_key"], ref["section_key"], ref["number"]) == ("04", "naming", n) and ref["text"] == f"Módulo 04 · §{n} · Convención de Nombres"
    f = _finding()
    assert f["finding_type"] == "NAMING_CONVENTION" and f["reference"] == {"module_key": "04", "section_key": "naming"}      # identidad: claves, no un número
    assert f["reference_text"] == ref["text"] and "§28" not in f["reference_text"] + f["evidence"]
    assert "no debe llevar prefijo de capa '4' (los artefactos tipados se nombran sin número de capa)" in f["evidence"]


# B + C. el MISMO payload, tras insertar una sección antes, renderiza el número nuevo; la clave semántica sobrevive
def test_inserting_a_section_before_naming_renumbers_the_same_finding_dynamically(monkeypatch):
    before = _finding()
    n = _naming_number()
    _renumbered_with_a_section_inserted_before_naming(monkeypatch)
    after = _finding()
    assert f"§{n}" in before["reference_text"] and f"§{n + 1}" in after["reference_text"]
    assert before["reference"] == after["reference"] == {"module_key": "04", "section_key": "naming"}                 # misma identidad semántica
    assert (before["finding_type"], before["title"], before["evidence"]) == (after["finding_type"], after["title"], after["evidence"])


# D. sin «§28» embebido en el camino del hallazgo de nombres
def test_no_hard_coded_section_number_remains_in_the_governed_naming_finding_path():
    for rel in ("webapp/services/asset_evaluation.py", "webapp/services/module_reference.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        code = re.sub(r'"""(?:.|\n)*?"""', "", src)
        code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
        assert not re.search(r"§\s*\d|secci[oó]n\s+\d{2}", code), rel
    assert "§" not in _finding()["evidence"] and "§" not in _finding()["title"]


# E. compatibilidad con payloads históricos
def test_legacy_ordinal_texts_are_translated_dynamically_and_never_crash(monkeypatch):
    n = _naming_number()
    assert mr.dynamic_text("Cumplen la sección 28 vigente") == f"Cumplen la sección {n} vigente"
    assert mr.dynamic_text("Artefactos bajo la convención de nombres de la sección 28") == f"Artefactos bajo la convención de nombres de la sección {n}"
    assert mr.dynamic_text("Sin ordinales") == "Sin ordinales" and mr.dynamic_text("") == ""
    assert mr.strip_ordinals("x (§29: regla de nombres)") == "x (regla de nombres)" and "§" not in mr.strip_ordinals("§28 uso no homologado")
    walked = mr.walk({"label": "No cumplen la sección 28 vigente", "n": 3, "items": [{"t": "§28"}]})
    assert walked["label"] == f"No cumplen la sección {n} vigente" and walked["n"] == 3 and walked["items"][0]["t"] == f"§{n}"
    # semántica gana: tras renumerar, el texto histórico sigue apuntando a la convención de nombres vigente
    _renumbered_with_a_section_inserted_before_naming(monkeypatch)
    assert mr.dynamic_text("Cumplen la sección 28 vigente") == f"Cumplen la sección {n + 1} vigente"


def test_unresolvable_references_never_show_a_stale_number(monkeypatch):
    monkeypatch.setattr(content_service, "load_module", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("registro roto")))
    assert mr.resolve_ref(mr.NAMING_CONVENTION)["text"] == "Módulo 04 · Convención de nombres"
    assert mr.resolve("99", "nope", label="Otra")["text"] == "Módulo 99 · Otra"
    assert mr.dynamic_text("Cumplen la sección 28 vigente") == "Cumplen la convención de nombres vigente" and "§" not in mr.dynamic_text("ver §28")


# F. visible en el informe (Assessment [04]) con el número vigente
def test_assessment_report_view_labels_follow_the_current_numbering():
    n = _naming_number()
    view = {"areas": [{"facts": {"headline": [{"label": "Cumplen la sección 28 vigente"}]}}], "summary": {}, "rule_results": [], "limitations": [],
            "observations": [], "review_required": [], "descriptive": [], "not_applicable": []}
    assert mr.walk(view)["areas"][0]["facts"]["headline"][0]["label"] == f"Cumplen la sección {n} vigente"
    assert presenter.MR is mr
