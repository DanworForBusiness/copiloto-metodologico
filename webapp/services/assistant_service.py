from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4
import os

from openai import OpenAI
import yaml

from config import DEFAULT_EXECUTION_MODE, FEEDBACK_LOG_DIR, MODEL, ia_enabled_for
from core.prompts import build_system_prompt
from webapp.services.content_service import ModuleDocument
from webapp.services.section_analysis import SectionAnalysisError, _legacy_api_key, render_llm_html


@dataclass(frozen=True)
class AssistantReply:
    id: str
    conversation_id: str
    question: str
    answer: str
    html: str
    module: str
    topic: str
    action: str


class StaleAssistantReplyError(RuntimeError):
    pass


@dataclass
class AssistantConversation:
    id: str
    module: str
    pack: str
    messages: list[tuple[str, str]] = field(default_factory=list)
    latest_reply_id: str = ""
    analysis_fingerprints: set[tuple[str, str]] = field(default_factory=set)


class AssistantConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, AssistantConversation] = {}
        self._replies: dict[str, AssistantReply] = {}
        self._lock = RLock()

    def start(self, module: str, pack: str) -> AssistantConversation:
        conversation = AssistantConversation(uuid4().hex, module, pack)
        with self._lock:
            self._conversations[conversation.id] = conversation
        return conversation

    def history(self, conversation_id: str, module: str, pack: str) -> tuple[tuple[str, str], ...]:
        conversation = self._conversation(conversation_id, module, pack)
        return tuple(conversation.messages[-12:])

    def claim_section_analysis(
        self,
        conversation_id: str,
        module: str,
        pack: str,
        section_slug: str,
        action: str,
    ) -> bool:
        """Atomically reserve a section/action pair for this conversation."""
        conversation = self._conversation(conversation_id, module, pack)
        fingerprint = (section_slug.strip(), action.strip())
        with self._lock:
            if fingerprint in conversation.analysis_fingerprints:
                return False
            conversation.analysis_fingerprints.add(fingerprint)
        return True

    def append(
        self,
        conversation_id: str,
        module: str,
        pack: str,
        question: str,
        answer: str,
        answer_html: str,
        *,
        topic: str,
        action: str,
        include_user: bool = True,
    ) -> AssistantReply:
        conversation = self._conversation(conversation_id, module, pack)
        reply = AssistantReply(
            uuid4().hex, conversation.id, question, answer, answer_html,
            module, topic, action,
        )
        with self._lock:
            if include_user:
                conversation.messages.append(("user", question))
            conversation.messages.append(("assistant", answer))
            self._replies[reply.id] = reply
            conversation.latest_reply_id = reply.id
        return reply

    def reply(self, reply_id: str) -> AssistantReply:
        with self._lock:
            reply = self._replies.get(reply_id)
        if reply is None:
            raise KeyError(reply_id)
        return reply

    def reply_for_feedback(self, reply_id: str) -> AssistantReply:
        with self._lock:
            reply = self._replies.get(reply_id)
            if reply is None:
                raise KeyError(reply_id)
            conversation = self._conversations.get(reply.conversation_id)
            if conversation is None:
                raise KeyError(reply.conversation_id)
            if conversation.latest_reply_id != reply_id:
                raise StaleAssistantReplyError(reply_id)
        return reply

    def _conversation(self, conversation_id: str, module: str, pack: str) -> AssistantConversation:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.module != module or conversation.pack != pack:
            raise KeyError(conversation_id)
        return conversation


class AssistantChatService:
    def __init__(
        self,
        client_factory: Callable[..., OpenAI] = OpenAI,
        execution_mode: str | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.execution_mode = execution_mode or DEFAULT_EXECUTION_MODE

    def ask(
        self,
        document: ModuleDocument,
        question: str,
        pack_name: str,
        history: tuple[tuple[str, str], ...],
        context: str | None = None,
    ) -> tuple[str, str]:
        question = (question or "").strip()
        if not question:
            raise SectionAnalysisError("Escribí una pregunta para continuar la conversación.")
        if len(question) > 4000:
            raise SectionAnalysisError("La consulta es demasiado extensa.")
        if not ia_enabled_for(document.summary.stem):
            raise SectionAnalysisError("El asistente no está habilitado para este módulo.")

        if self.execution_mode.lower().startswith("simul"):
            answer = (
                "🧪 **Simulación activa** — recibí tu consulta.\n\n"
                "- La conversación conserva el contexto del módulo.\n"
                "- No se consumieron créditos."
            )
            return answer, render_llm_html(answer)

        api_key = (os.getenv("OPENAI_API_KEY") or "").strip() or _legacy_api_key()
        if not api_key:
            raise SectionAnalysisError("Falta configurar OPENAI_API_KEY para usar el asistente.")
        system_prompt = build_system_prompt(
            document.summary.stem, pack_name=pack_name, user_context={},
        )
        conversation = "\n".join(
            f"{'Usuario' if role == 'user' else 'Arquitecto Danwor'}: {text}"
            for role, text in history
        )
        user_prompt = (
            f"CONTEXTO (Marco Danwor)\n{'-' * 40}\n{context or document.source}\n\n"
            f"CONVERSACION PREVIA\n{'-' * 40}\n{conversation or '(sin mensajes previos)'}\n\n"
            f"NUEVA CONSULTA\n{'-' * 40}\n{question}\n\n"
            "FORMATO DE RESPUESTA\n"
            f"{'-' * 40}\n"
            "Respond\u00e9 en Markdown v\u00e1lido y legible. Si el contexto incluye una estructura, "
            "lista de definiciones, convenci\u00f3n de nombres, patr\u00f3n o pasos, conserv\u00e1 esa "
            "estructura en la respuesta: us\u00e1 p\u00e1rrafos separados, bullets con etiquetas en "
            "negrita y ejemplos en l\u00edneas propias. No lo conviertas en un p\u00e1rrafo plano. "
            "Si mostr\u00e1s una plantilla con placeholders entre <...>, dejala en una l\u00ednea "
            "separada y explic\u00e1 cada placeholder debajo con bullets."
        )
        try:
            client = self.client_factory(api_key=api_key)
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=1200,
            )
            answer = str(response.choices[0].message.content or "").strip()
        except Exception as exc:
            raise SectionAnalysisError(
                "No pudimos completar la consulta. Intentá nuevamente."
            ) from exc
        if not answer:
            raise SectionAnalysisError("El modelo no devolvió contenido.")
        return answer, render_llm_html(answer)


class AssistantFeedbackRepository:
    def __init__(self, root: Path = FEEDBACK_LOG_DIR) -> None:
        self.root = Path(root)

    def save(self, reply: AssistantReply, rating: str, comment: str = "") -> Path:
        if rating not in {"up", "down"}:
            raise ValueError("Rating inválido")
        if rating == "down" and not comment.strip():
            raise ValueError("Contanos brevemente qué deberíamos mejorar.")
        self.root.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = self.root / f"feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        payload = {
            "type": "assistant_response",
            "module": reply.module,
            "topic": reply.topic,
            "action": reply.action,
            "rating": rating,
            "comment": comment.strip(),
            "question": reply.question[:300],
            "ts": now.isoformat(),
        }
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
        return path
