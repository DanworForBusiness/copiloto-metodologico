import yaml
import pytest

from webapp.services.case_library import CaseLibraryRepository
from webapp.services.wizard_service import WizardStore, _all_trees

def test_real_yaml_tree_advances_and_goes_back():
    store = WizardStore(); tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]; run = store.start(tree)
    store.answer(run.id, "Warehouse/Lakehouse")
    assert run.current == "q2"
    store.back(run.id)
    assert run.current == "q1"

def test_wizard_blocks_self_loop():
    # Guard de seguridad: si un YAML define una transición que vuelve al mismo
    # nodo, se bloquea. Se ejercita con un árbol sintético (los árboles reales ya
    # no tienen self-loops; antes este caso vivía en el árbol de módulo 01).
    store = WizardStore()
    tree = {
        "id": "t_loop", "title": "Loop", "start": "q1",
        "nodes": {"q1": {"question": "¿?", "options": ["A"], "next": {"A": "q1"}}},
        "leaves": {},
    }
    run = store.start(tree)
    store.answer(run.id, "A")
    assert run.current == "q1"
    assert "ciclo" in run.error


def test_flujos_sap_abap_transition_reaches_q2_b():
    # Root cause q2_origen: la opción visible "SAP/ABAP" tenía como clave de
    # transición "ERP SAP", que no matchea ninguna opción. wizard_service resuelve
    # next[answer] literal → next["SAP/ABAP"] era None → "no define una transición".
    store = WizardStore()
    tree = store.tree_by_id("m04_flujos_de_datos")
    run = store.start(tree)
    store.answer(run.id, "Sí")            # q1_delta -> q2_origen
    assert run.current == "q2_origen"
    store.answer(run.id, "SAP/ABAP")      # q2_origen -> q2_b
    assert run.error == ""
    assert run.current == "q2_b"


def test_flujos_delta_sibling_paths_intact():
    # Los otros dos caminos del mismo nodo no deben verse afectados por el fix.
    store = WizardStore()
    tree = store.tree_by_id("m04_flujos_de_datos")

    run = store.start(tree)
    store.answer(run.id, "Sí")
    store.answer(run.id, "Datasphere")
    assert run.error == ""
    assert run.current == "leaf_transformation_flow_delta"

    run = store.start(tree)
    store.answer(run.id, "Sí")
    store.answer(run.id, "Otra")
    assert run.error == ""
    assert run.current == "q2c_conector"


def test_flujos_sap_abab_full_path_to_leaf():
    # Camino completo hasta recomendación desde la rama SAP/ABAP recién reparada.
    store = WizardStore()
    tree = store.tree_by_id("m04_flujos_de_datos")
    run = store.start(tree)
    store.answer(run.id, "Sí")            # -> q2_origen
    store.answer(run.id, "SAP/ABAP")      # -> q2_b
    store.answer(run.id, "Sí")            # -> leaf_replication_flow
    assert run.error == ""
    assert run.leaf is not None
    assert run.current == "leaf_replication_flow"


def test_choice_nodes_options_match_transition_keys():
    """Invariante estructural del root cause de q2_origen: en cada nodo de
    decisión (single_choice/choice con mapa `next`), cada opción seleccionable
    tiene su clave de transición correspondiente y no existen claves huérfanas.

    Alcance deliberadamente acotado a este contrato (options keys == next keys).
    NO valida que los targets existan como nodo/leaf: eso es otro bug distinto
    (dangling targets en 01_espacios_topologia) que se trata en su propio ticket.
    Los nodos de assessment (con `scores`) mapean por puntaje, no por transición,
    y quedan fuera de esta invariante."""
    offenders = []
    for tree in _all_trees("datasphere_core"):
        for node_id, node in (tree.get("nodes") or {}).items():
            if not isinstance(node, dict):
                continue
            if str(node.get("type") or "") not in {"single_choice", "choice"}:
                continue
            transitions = node.get("next")
            if not isinstance(transitions, dict) or node.get("scores"):
                continue
            options = {str(option) for option in (node.get("options") or [])}
            keys = {str(key) for key in transitions}
            missing = options - keys
            orphan = keys - options
            if missing or orphan:
                offenders.append((tree.get("id"), node_id, sorted(missing), sorted(orphan)))
    assert offenders == [], f"opciones sin transición / claves huérfanas: {offenders}"


def test_module_03_exposes_three_operational_trees():
    trees = WizardStore.trees_for_module("03_Integracion_y_Estrategia_de_Datos")
    assert [tree["title"] for tree in trees] == [
        "Federación vs Persistencia vs Híbrido",
        "Opciones de Extracción",
        "Resolución de Contrato",
    ]


def test_completed_tree_accepts_feedback_and_restart_clears_it():
    store = WizardStore()
    tree = store.trees_for_module("01_Arquitectura_y_Conectividad")[0]
    run = store.start(tree)
    store.answer(run.id, "Warehouse/Lakehouse")
    store.answer(run.id, "2-Tier")
    store.answer(run.id, "Sí")
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
    store.answer(run.id, "Sí")
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
