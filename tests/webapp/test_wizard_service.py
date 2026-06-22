import yaml
import pytest

from webapp.services.case_library import CaseLibraryRepository
from webapp.services.wizard_service import WizardStore

def test_real_yaml_tree_advances_and_goes_back():
    store = WizardStore(); tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]; run = store.start(tree)
    store.answer(run.id, "Warehouse/Lakehouse")
    assert run.current == "q2"
    store.back(run.id)
    assert run.current == "q1"

def test_wizard_blocks_legacy_self_loop():
    store = WizardStore(); tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]; run = store.start(tree)
    for answer in ("Warehouse/Lakehouse", "1-Tier", "No", "Excel"): store.answer(run.id, answer)
    assert run.current == "q4"
    assert "ciclo" in run.error


def test_module_03_exposes_three_operational_trees():
    trees = WizardStore.trees_for_module("03_Integracion_y_Estrategia_de_Datos")
    assert [tree["title"] for tree in trees] == [
        "Federación vs Persistencia",
        "Opciones de Extracción",
        "Resolución de Contrato",
    ]


def test_completed_tree_accepts_feedback_and_restart_clears_it():
    store = WizardStore()
    tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]
    run = store.start(tree)
    store.answer(run.id, "Warehouse/Lakehouse")
    store.answer(run.id, "2-Tier")
    assert run.leaf is not None

    store.rate(run.id, "up")
    assert run.feedback_rating == "up"

    store.restart(run.id)
    assert run.feedback_rating == ""


def _complete_short_tree(store: WizardStore):
    tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]
    run = store.start(tree)
    store.answer(run.id, "Warehouse/Lakehouse")
    store.answer(run.id, "2-Tier")
    return run


def test_positive_feedback_updates_case_library(tmp_path):
    store = WizardStore(case_repository=CaseLibraryRepository(tmp_path))
    run = _complete_short_tree(store)
    case_path = tmp_path / f"{run.case_id}.yaml"
    assert case_path.exists()

    store.submit_feedback(run.id, rating="up")
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    assert case["rating"] == "up"
    assert case["reviewed"] is True
    assert case["feedback_at"]


def test_negative_feedback_requires_lesson_and_persists_form(tmp_path):
    store = WizardStore(case_repository=CaseLibraryRepository(tmp_path))
    run = _complete_short_tree(store)

    with pytest.raises(ValueError, match="lección clave"):
        store.submit_feedback(run.id, rating="down", outcome="no anduvo")

    store.submit_feedback(
        run.id,
        rating="down",
        outcome="no anduvo",
        context="ECC 6.07",
        detail="La alternativa no era compatible.",
        lesson="Validar la versión ABAP.",
    )
    case = yaml.safe_load((tmp_path / f"{run.case_id}.yaml").read_text(encoding="utf-8"))
    assert case["rating"] == "down"
    assert case["outcome"] == "no anduvo"
    assert case["context_description"] == "ECC 6.07"
    assert case["decision_taken"] == "La alternativa no era compatible."
    assert case["lessons"] == ["Validar la versión ABAP."]
