"""
test_reaction_subject.py — USAGE METRICS REACCIONES: "objeto reaccionado"
(owner ticket "REACCIONES — PER-USER REACTED-OBJECT BREAKDOWN").

Prueba `activity_analytics._reaction_subject` en aislamiento total (función
pura, sin I/O) — el resolver determinístico que decide, por productor, A QUÉ
objeto concreto corresponde una reacción, o si esa familia genuinamente no
tiene uno (nunca fabricado)."""
from __future__ import annotations

from webapp.services import activity_analytics as aa


def _s(event, module, payload):
    return aa._reaction_subject(event, module, payload)


# ── C: reporting resuelve por report_tab ────────────────────────────────────

def test_reporting_feedback_subject_uses_report_tab_and_title():
    key, label = _s("reporting.feedback", "reporting", {"report_tab": "rentabilidad", "report_title": "Rentabilidad"})
    assert label == "Rentabilidad"
    key2, label2 = _s("reporting.feedback", "reporting", {"report_tab": "cartera", "report_title": "Cartera / Proyectos"})
    assert key != key2  # dos pestañas distintas, dos subjects distintos.


def test_reporting_feedback_falls_back_to_raw_tab_without_title():
    # Compatibilidad con un evento sin `report_title` (p. ej. viejo, previo
    # a este corrective) -- nunca revienta, cae al tab crudo.
    key, label = _s("reporting.feedback", "reporting", {"report_tab": "cartera"})
    assert label == "cartera"


def test_reporting_feedback_without_tab_has_no_subject():
    assert _s("reporting.feedback", "reporting", {}) is None


# ── staffing: sin sub-identidad real, NUNCA un subject fabricado ───────────

def test_staffing_feedback_has_no_subject():
    assert _s("staffing.feedback", "OP01", {}) is None


# module.feedback -----------------------------------------------------------

def test_module_feedback_subject_uses_module_label():
    key, label = _s("module.feedback", "CP01", {})
    assert key == "module:CP01"
    assert label  # module_label ya probado en otros lados; solo confirmamos que resuelve.


def test_module_feedback_without_module_has_no_subject():
    assert _s("module.feedback", "", {}) is None


# ── B: section resuelve al padre + detalle ──────────────────────────────────

def test_section_feedback_subject_combines_module_and_section_title():
    key, label = _s("section.feedback", "CP01", {"section": "espacios-transportes", "section_title": "Espacios y Transportes"})
    assert "Espacios y Transportes" in label
    assert key == "section:CP01:espacios-transportes"


def test_section_feedback_falls_back_to_slug_without_title():
    key, label = _s("section.feedback", "CP01", {"section": "espacios-transportes"})
    assert "espacios-transportes" in label


def test_section_feedback_without_section_or_module_has_no_subject():
    assert _s("section.feedback", "CP01", {}) is None  # sin `section` en payload -- backfill histórico.
    assert _s("section.feedback", "", {"section": "x"}) is None


# ── E: catálogo resuelve por kind/ref, kind crudo (MISMO criterio que
# catalog_view en Eventos) ───────────────────────────────────────────────────

def test_catalog_feedback_subject_combines_kind_and_ref():
    key, label = _s("catalog.feedback", "", {"kind": "dominio", "ref": "ventas"})
    assert label == "dominio · ventas"
    assert key == "catalog:dominio:ventas"


def test_catalog_feedback_subject_without_ref_uses_kind_only():
    key, label = _s("catalog.feedback", "", {"kind": "guide"})
    assert label == "guide"


def test_catalog_feedback_without_kind_has_no_subject():
    assert _s("catalog.feedback", "", {}) is None  # backfill histórico -- aún sin enriquecer.


def test_catalog_section_feedback_subject_combines_cliente_and_dominio():
    key, label = _s("catalog_section.feedback", "", {"cliente": "AySA", "dominio": "Ventas", "section_slug": "resumen"})
    assert label == "AySA · Ventas"
    key2, _ = _s("catalog_section.feedback", "", {"cliente": "AySA", "dominio": "Compras", "section_slug": "resumen"})
    assert key != key2  # distinto dominio, distinto subject.


def test_catalog_section_feedback_without_cliente_or_dominio_has_no_subject():
    assert _s("catalog_section.feedback", "", {}) is None


# ── D: academy prefiere el título capturado, cae al module_label ───────────

def test_academy_feedback_subject_prefers_captured_title():
    key, label = _s("academy.feedback", "ACAD01_Paradigmas", {"academy_title": "Paradigmas de datos"})
    assert label == "Paradigmas de datos"


def test_academy_feedback_falls_back_to_module_label_without_title():
    # Backfill histórico: solo `module` (el stem), sin `academy_title`.
    key, label = _s("academy.feedback", "ACAD01_Paradigmas", {})
    assert label  # nunca vacío -- cae al stem crudo vía module_label.


def test_academy_feedback_without_module_has_no_subject():
    assert _s("academy.feedback", "", {}) is None


# assistant.feedback ---------------------------------------------------------

def test_assistant_feedback_subject_uses_module_label():
    key, label = _s("assistant.feedback", "CP01", {"reply_id": "r1"})
    assert key == "assistant:CP01"
    assert label


def test_assistant_feedback_without_module_has_no_subject():
    assert _s("assistant.feedback", "", {}) is None


# ── tree ─────────────────────────────────────────────────────────────────

def test_tree_feedback_subject_prefers_tree_title():
    key, label = _s("tree.feedback", "CP01", {"tree_id": "m01_espacios", "tree_title": "Topología de Espacios"})
    assert label == "Topología de Espacios"
    assert key == "tree:m01_espacios"


def test_tree_feedback_falls_back_to_tree_id_without_title():
    key, label = _s("tree.feedback", "CP01", {"tree_id": "m01_espacios"})
    assert label == "m01_espacios"


def test_tree_feedback_without_tree_id_has_no_subject():
    assert _s("tree.feedback", "CP01", {}) is None  # nunca ocurre en la práctica -- ver AUDIT.


# ── unknown/futuro sin resolver acá -- nunca "General"/"Otro" ──────────────

def test_unrecognized_event_has_no_subject():
    assert _s("commercial.feedback", "CM01", {"capability": "leads"}) is None
    assert _s("some_future_event", "X", {}) is None
