import pytest
import yaml

from webapp.services.assistant_service import (
    AssistantChatService,
    AssistantConversationStore,
    AssistantFeedbackRepository,
    StaleAssistantReplyError,
)
from webapp.services.content_service import load_module


def test_conversation_keeps_section_and_free_chat_history():
    store = AssistantConversationStore()
    conversation = store.start("01_Arquitectura_y_Conectividad", "datasphere_core")
    reply = store.append(
        conversation.id,
        conversation.module,
        conversation.pack,
        "Consulta inicial",
        "Respuesta inicial",
        "<p>Respuesta inicial</p>",
        topic="Referencias",
        action="Salida completa",
    )

    assert store.reply(reply.id) == reply
    assert store.history(conversation.id, conversation.module, conversation.pack) == (
        ("user", "Consulta inicial"),
        ("assistant", "Respuesta inicial"),
    )


def test_only_latest_assistant_reply_can_receive_feedback():
    store = AssistantConversationStore()
    conversation = store.start("01_Arquitectura_y_Conectividad", "datasphere_core")
    first = store.append(
        conversation.id, conversation.module, conversation.pack,
        "Primera pregunta", "Primera respuesta", "<p>Primera respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )
    latest = store.append(
        conversation.id, conversation.module, conversation.pack,
        "Pregunta siguiente", "Respuesta siguiente", "<p>Respuesta siguiente</p>",
        topic="Pregunta libre", action="Asistente",
    )

    with pytest.raises(StaleAssistantReplyError):
        store.reply_for_feedback(first.id)
    assert store.reply_for_feedback(latest.id) == latest


def test_section_analysis_fingerprint_can_only_be_claimed_once():
    store = AssistantConversationStore()
    conversation = store.start("01_Arquitectura_y_Conectividad", "datasphere_core")

    assert store.claim_section_analysis(
        conversation.id, conversation.module, conversation.pack,
        "configuracion-de-espacios", "Checklist ejecutable",
    )
    assert not store.claim_section_analysis(
        conversation.id, conversation.module, conversation.pack,
        "configuracion-de-espacios", "Checklist ejecutable",
    )
    assert store.claim_section_analysis(
        conversation.id, conversation.module, conversation.pack,
        "configuracion-de-espacios", "Decisiones si/entonces",
    )


def test_free_chat_simulation_returns_rendered_markdown():
    service = AssistantChatService(execution_mode="Simulación")
    document = load_module("01_Arquitectura_y_Conectividad")

    answer, rendered = service.ask(document, "¿Cómo continúo?", "datasphere_core", ())

    assert "Simulación activa" in answer
    assert "<ul>" in rendered
    assert rendered.count("<li>") == 2


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
            content = "Respuesta general de prueba."

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


def test_directive_inyecta_bloque_prioritario_y_saltea_grounding(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _CapturingClient.captured = {}
    # centinela: si el grounding se inyectara, aparecería en el prompt.
    monkeypatch.setattr(
        "webapp.services.assistant_service._catalog_grounding",
        lambda _q: "GROUNDING_CENTINELA_NO_DEBE_APARECER",
    )
    service = AssistantChatService(
        client_factory=_CapturingClient, execution_mode="produccion",
    )
    document = load_module("01_Arquitectura_y_Conectividad")

    service.ask(
        document, "tenemos algo con model import = SI?", "datasphere_core", (),
        directive="Explicá a nivel producto SAP Datasphere, no repitas el 'no' del catálogo.",
    )

    user_prompt = _CapturingClient.captured["messages"][1]["content"]
    assert "DIRECTIVA (prioritaria)" in user_prompt
    assert user_prompt.lstrip().startswith("DIRECTIVA")
    assert "no repitas el 'no' del catálogo" in user_prompt
    # con directiva, el grounding del inventario NO debe inyectarse
    assert "GROUNDING_CENTINELA_NO_DEBE_APARECER" not in user_prompt


def test_assistant_feedback_persists_up_and_down(tmp_path):
    store = AssistantConversationStore()
    conversation = store.start("01_Arquitectura_y_Conectividad", "datasphere_core")
    reply = store.append(
        conversation.id, conversation.module, conversation.pack,
        "Pregunta", "Respuesta", "<p>Respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )
    repository = AssistantFeedbackRepository(tmp_path)

    positive = yaml.safe_load(repository.save(reply, "up").read_text(encoding="utf-8"))
    negative = yaml.safe_load(
        repository.save(reply, "down", "Faltó un ejemplo").read_text(encoding="utf-8")
    )

    assert positive["rating"] == "up"
    assert negative["rating"] == "down"
    assert negative["comment"] == "Faltó un ejemplo"
    assert "answer" not in negative
