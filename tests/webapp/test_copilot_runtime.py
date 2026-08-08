"""
test_copilot_runtime.py — Contrato de ownership/identidad de
webapp/copilot_runtime.py (Fase 2J-A).

Prueba que `store`/`assistant_store` tienen la clase esperada, que cada
router que los consume (`wizard`, `assistant`, `modules`) expone la MISMA
identidad de objeto (no una copia) importándolos directo de
`webapp.copilot_runtime` — sin alias en `webapp.main` (removidos en Fase
2J-D, sin consumidores de producción restantes) — y que el estado creado a
través de una referencia es visible a través de otra.
"""
from webapp import copilot_runtime
from webapp.routers import wizard as wizard_router
from webapp.routers import assistant as assistant_router
from webapp.routers import modules as modules_router
from webapp.services.wizard_service import WizardStore
from webapp.services.assistant_service import AssistantConversationStore


def test_store_is_wizard_store_instance():
    assert isinstance(copilot_runtime.store, WizardStore)


def test_assistant_store_is_assistant_conversation_store_instance():
    assert isinstance(copilot_runtime.assistant_store, AssistantConversationStore)


def test_wizard_router_store_alias_is_same_object_as_copilot_runtime():
    assert wizard_router.store is copilot_runtime.store


def test_modules_router_store_alias_is_same_object_as_copilot_runtime():
    assert modules_router.store is copilot_runtime.store


def test_assistant_router_assistant_store_alias_is_same_object_as_copilot_runtime():
    assert assistant_router.assistant_store is copilot_runtime.assistant_store


def test_modules_router_assistant_store_alias_is_same_object_as_copilot_runtime():
    assert modules_router.assistant_store is copilot_runtime.assistant_store


def test_wizard_run_started_via_one_reference_is_visible_via_the_other():
    trees = copilot_runtime.store.trees_for_module("01_Arquitectura_y_Conectividad")
    assert trees, "el módulo de prueba debería tener al menos un árbol"
    run = copilot_runtime.store.start(trees[0])
    # Misma identidad → el run creado vía copilot_runtime es recuperable vía
    # el alias que webapp/routers/wizard.py usa hoy.
    same_run = wizard_router.store.get(run.id)
    assert same_run is run


def test_assistant_conversation_started_via_one_reference_is_visible_via_the_other():
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core",
    )
    assistant_router.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "pregunta de prueba", "respuesta de prueba", "<p>respuesta de prueba</p>",
        topic="Prueba", action="Prueba",
    )
    # Leído vía la otra referencia: misma identidad, no una copia separada.
    history = copilot_runtime.assistant_store.history(
        conversation.id, conversation.module, conversation.pack,
    )
    assert ("assistant", "respuesta de prueba") in history
