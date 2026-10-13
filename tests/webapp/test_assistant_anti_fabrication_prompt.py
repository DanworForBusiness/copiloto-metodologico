"""Regla anti-fabricación del chat libre (defensa COMPLEMENTARIA): el prompt siempre indica que los puntajes, rankings y estados de los modelos del Catálogo son datos reales que el LLM no
conoce salvo los que figuren en el bloque de catálogo del mensaje. No bloquea explicaciones conceptuales ni ejemplos hipotéticos rotulados. El resto del prompt y el disclaimer no cambian.

La integridad de los datos gobernados NO depende de este texto (la garantizan las consultas deterministas); estos tests fijan solo el contrato del prompt. Cliente OpenAI FALSO."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from core.prompts import build_system_prompt
from webapp.services import assistant_service as svc
from webapp.services import auth_service
from webapp.services.assistant_service import DATA_INTEGRITY_RULE, AssistantChatService
from webapp.services.content_service import load_module

STEM = "01_Arquitectura_y_Conectividad"
PACK = "datasphere_core"


class _CapturingClient:
    """Cliente OpenAI falso: captura los mensajes y devuelve una respuesta canónica."""

    captured: dict = {}

    def __init__(self, **_kwargs):
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, *, messages, **_kwargs):
        _CapturingClient.captured["messages"] = messages

        class _Msg:
            content = "Respuesta de prueba."

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(svc, "_catalog_grounding", lambda _q: "")
    _CapturingClient.captured = {}
    return AssistantChatService(client_factory=_CapturingClient, execution_mode="produccion")


def _ask(service, question="¿Cuál es el modelo con mejor puntaje?", **kwargs) -> tuple[str, str]:
    service.ask(load_module(STEM), question, PACK, (), **kwargs)
    system, user = (m["content"] for m in _CapturingClient.captured["messages"])
    return system, user


def test_the_rule_is_in_the_normal_prompt_between_the_question_and_the_format_section(service):
    _system, user = _ask(service)
    assert user.count(DATA_INTEGRITY_RULE) == 1
    assert user.index("NUEVA CONSULTA") < user.index(DATA_INTEGRITY_RULE) < user.index("FORMATO DE RESPUESTA")


def test_the_rule_is_also_present_with_general_true_and_without_the_module_context(service):
    _system, user = _ask(service, general=True)
    assert DATA_INTEGRITY_RULE in user
    assert "CONTEXTO (Marco Danwor)" not in user                                         # general=True sigue sin inyectar el módulo activo


def test_the_rule_is_also_present_with_a_directive_which_stays_first(service):
    _system, user = _ask(service, directive="Explicá a nivel producto SAP Datasphere.")
    assert DATA_INTEGRITY_RULE in user
    assert user.lstrip().startswith("DIRECTIVA (prioritaria)")


def test_the_rule_does_not_replace_the_catalog_grounding_block(service, monkeypatch):
    monkeypatch.setattr(svc, "_catalog_grounding", lambda _q: "CATALOGO_CENTINELA")
    _system, user = _ask(service)
    assert "CATALOGO_CENTINELA" in user and DATA_INTEGRITY_RULE in user
    assert user.index("CATALOGO_CENTINELA") < user.index(DATA_INTEGRITY_RULE)


@pytest.mark.parametrize("kwargs", [{}, {"general": True}, {"directive": "Explicá a nivel producto."}])
def test_the_rest_of_the_prompt_is_unchanged(service, monkeypatch, kwargs):
    """Quitar la sección de la regla deja EXACTAMENTE el prompt anterior; el system prompt no cambia."""
    system, user = _ask(service, **kwargs)
    expected_system = build_system_prompt(None if kwargs.get("general") else STEM, pack_name=PACK, user_context={})
    assert system == expected_system
    monkeypatch.setattr(svc, "DATA_INTEGRITY_RULE", "")
    base_system, base_user = _ask(service, **kwargs)
    assert base_system == system
    assert user.replace(f"{DATA_INTEGRITY_RULE}\n\n", "\n\n") == base_user


def test_the_rule_forbids_presenting_invented_data_as_real_but_keeps_concepts_and_hypotheticals_allowed():
    rule = DATA_INTEGRITY_RULE
    for must in ("no los estimes", "no los inventes", "no tenés acceso a ese dato", "se consulta en el Catálogo", "datos reales"):
        assert must in rule
    for allowed in ("podés explicar conceptos y metodología", "ejemplo hipotético", "ilustrativo"):
        assert allowed.lower() in rule.lower()
    assert "no respondas" not in rule.lower() and "nunca respondas" not in rule.lower()    # no es una negativa general


def test_the_simulation_mode_still_answers_without_calling_the_client(monkeypatch):
    monkeypatch.setattr(svc, "default_client", lambda **_k: pytest.fail("no debe llamar al proveedor"))
    answer, _html = AssistantChatService(execution_mode="Simulación").ask(load_module(STEM), "¿Cómo continúo?", PACK, ())
    assert "Simulación activa" in answer


def test_end_to_end_a_free_answer_keeps_the_ia_disclaimer_and_carries_the_rule(monkeypatch):
    from webapp.routers.assistant import assistant_chat_service, assistant_store
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(svc, "_catalog_grounding", lambda _q: "")
    monkeypatch.setattr(assistant_chat_service, "client_factory", _CapturingClient)
    monkeypatch.setattr(assistant_chat_service, "execution_mode", "produccion")
    _CapturingClient.captured = {}
    client = TestClient(main.app)
    client.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    conversation = assistant_store.start(STEM, PACK).id
    page = client.post(f"/modules/{STEM}/assistant/chat",
                       data={"question": "qwxz plugh frobnicate", "conversation_id": conversation, "pack": PACK}).text
    assert "Respuesta de prueba." in page                                                 # contestó el LLM falso: no hubo ruta determinista
    assert "Respuesta generada con IA" in page                                            # el disclaimer se conserva
    assert DATA_INTEGRITY_RULE in _CapturingClient.captured["messages"][1]["content"]
