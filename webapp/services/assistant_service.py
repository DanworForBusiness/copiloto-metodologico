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
from core import remote_store
from core.prompts import build_system_prompt


def _grounding_text(matches: list) -> str:
    """Bloque de grounding del catálogo para el LLM: SOLO IDENTIDAD (qué flujos existen, de qué cliente y cuántos artefactos tienen).

    Deliberadamente NO incluye el veredicto legado del catálogo («mirada Danwor»: plena/buena/parcial), su ícono ni su texto de aptitud: son una clasificación histórica del generador, no una evaluación
    vigente, y con ellos el LLM los presentaba como si fueran el Assessment del modelo (o su Juicio metodológico). Ningún dato de evaluación (Juicio, confianza, cobertura, estado) viaja en este bloque: los
    contratos gobernados los responden las consultas deterministas o el propio Assessment, y acá se declara explícitamente que no están (ver ``DATA_INTEGRITY_RULE``)."""
    if not matches:
        return ""
    lines = [
        "FLUJOS PUBLICADOS EN EL CATÁLOGO (solo identidad)",
        "Esta lista indica ÚNICAMENTE qué flujos existen, de qué cliente son y cuántos artefactos tienen. NO contiene el Assessment ni el Juicio metodológico de ningún modelo, ni su adopción, "
        "confianza, cobertura o estado, y no permite deducirlos. Si te piden alguno de esos datos, decí que no lo tenés en esta conversación y que se consulta en el Assessment del modelo, "
        "desde el Catálogo. No inventes flujos: usá solo los de esta lista. Ofrecé verlos por cliente si hay varios.",
        "",
    ]
    lines += [f"- {e.dominio} · cliente {e.cliente} ({len(e.artefactos)} artefactos)" for e in matches]
    return "\n".join(lines)


def _catalog_grounding(query: str) -> str:
    """PILOTO (local, no productivo): si existe un catálogo CDS local, devuelve el
    bloque de grounding (identidad de los flujos publicados, sin ninguna evaluación) para
    inyectar en el prompt del asistente. Gateado por DANWOR_CATALOG_DIR o, por
    defecto, <repo>/catalog_local. No-op si la carpeta no existe → cero riesgo."""
    try:
        import sys
        from pathlib import Path
        from config import BASE_DIR
        raw = os.getenv("DANWOR_CATALOG_DIR", "").strip()
        cat_dir = Path(raw) if raw else (BASE_DIR / "catalog_local")
        if not cat_dir.exists():
            return ""
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in sys.path:
            sys.path.insert(0, svc_dir)
        import catalog_service as mod
        # Fuente: SharePoint en vivo (token delegado) o carpeta local (default).
        if os.getenv("DANWOR_CATALOG_SOURCE", "local").strip().lower() == "sharepoint":
            token = os.getenv("DANWOR_CATALOG_GRAPH_TOKEN", "").strip()
            entries = mod.fetch_catalog_from_sharepoint(token)
        else:
            entries = mod.load_catalog(cat_dir)
        return _grounding_text(mod.search_catalog(query, entries))
    except Exception:
        return ""
from webapp.services.assistant_context import QueryState, touch
from webapp.services.content_service import ModuleDocument
from webapp.services.llm_client import classify_openai_error, default_client
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
    query_state: QueryState | None = None       # contexto ESTRUCTURADO de la última consulta (ver assistant_context); en memoria, mismo ciclo de vida que la conversación


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

    # ── contexto estructurado de la última consulta (paso 3) ─────────────────────────────────────────────────────────────────────
    # Pertenece a UN usuario y a ESTA conversación: otro usuario nunca lo lee ni lo borra. No guarda URLs ni valores (ver assistant_context).
    def query_state(self, conversation_id: str, module: str, pack: str, user_sub: str) -> QueryState | None:
        conversation = self._conversation(conversation_id, module, pack)
        with self._lock:
            state = conversation.query_state
        return state if (state is not None and user_sub and state.user_sub == user_sub) else None

    def set_query_state(self, conversation_id: str, module: str, pack: str, state: QueryState) -> None:
        conversation = self._conversation(conversation_id, module, pack)
        with self._lock:
            conversation.query_state = state

    def clear_query_state(self, conversation_id: str, module: str, pack: str, user_sub: str) -> None:
        conversation = self._conversation(conversation_id, module, pack)
        with self._lock:
            if conversation.query_state is not None and conversation.query_state.user_sub == user_sub:
                conversation.query_state = None

    def touch_query_state(self, conversation_id: str, module: str, pack: str, user_sub: str, now: float) -> None:
        """Renueva el instante del contexto (el TTL corre desde la última actividad) sin tocar nada más."""
        conversation = self._conversation(conversation_id, module, pack)
        with self._lock:
            state = conversation.query_state
            if state is not None and state.user_sub == user_sub:
                conversation.query_state = touch(state, now)

    def _conversation(self, conversation_id: str, module: str, pack: str) -> AssistantConversation:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.module != module or conversation.pack != pack:
            raise KeyError(conversation_id)
        return conversation


# Regla de integridad de datos del Catálogo, SIEMPRE presente en el prompt del chat libre (también con directiva y con general=True). Defensa COMPLEMENTARIA: la integridad de los
# datos gobernados la garantizan las consultas deterministas, no este texto. No bloquea explicaciones conceptuales ni ejemplos hipotéticos explícitos; solo impide presentar
# puntajes, rankings o estados inventados como datos reales del Catálogo.
DATA_INTEGRITY_RULE = (
    "REGLA DE DATOS DEL CATÁLOGO\n"
    f"{'-' * 40}\n"
    "Los puntajes, rankings, niveles de evaluación, coberturas, niveles de confianza, conteos y estados de los modelos del Catálogo Danwor "
    "(por ejemplo, cuál es el «mejor» o el «peor» modelo) son datos reales que NO conocés, salvo los que figuren expresamente, con su fuente, en este "
    "mensaje. Si te piden esos datos como reales y no figuran acá, decí con claridad que no tenés acceso a ese dato en esta "
    "conversación y que se consulta en el Catálogo; no los estimes, no los inventes ni los presentes como reales. "
    "Sí podés explicar conceptos y metodología, y dar un ejemplo hipotético si el usuario lo pide, siempre rotulado de forma explícita como "
    "ilustrativo y nunca como dato del Catálogo.\n\n"
    "TRES COSAS QUE NO SE CONFUNDEN\n"
    "- Veredicto legado del catálogo («mirada Danwor»: plena, buena o parcial): es una clasificación histórica del generador del catálogo, NO una evaluación vigente. "
    "Nunca lo presentes como el Assessment, como la adopción metodológica ni como el estado de un modelo.\n"
    "- Juicio metodológico vigente (Adopción metodológica: Plena, Buena, Adecuada, Parcial o No determinable): solo existe si un Assessment vigente lo respalda; "
    "no lo deduzcas de ninguna otra clasificación.\n"
    "- Estado del Assessment (vigente, desactualizado, sin assessment, no aplica, no disponible o no compatible): describe la vigencia del informe, no la calidad del modelo.\n"
    "Si te piden ver o resumir el assessment, la adopción, la confianza o el estado de un modelo y esos datos no figuran en este mensaje, decí que no tenés acceso a ese dato "
    "en esta conversación y que se consulta en el Assessment del modelo, desde el Catálogo. No uses emojis de color ni semáforos para representar niveles: los colores los define el producto."
)


class AssistantChatService:
    def __init__(
        self,
        client_factory: Callable[..., OpenAI] = default_client,
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
        directive: str = "",
        *,
        general: bool = False,
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
        # general=True (ver "Explicame vos" sobre una respuesta sin evidencia local
        # en ningún módulo, AssistantRoutingService.no_local_evidence_explain): un
        # module_stem=None no inyecta el scope block del módulo activo (ver
        # core.prompts.build_system_prompt) -- forzar el scope de un módulo que
        # justamente NO cubre el tema volvería a bloquear la respuesta general.
        system_prompt = build_system_prompt(
            None if general else document.summary.stem, pack_name=pack_name, user_context={},
        )
        conversation = "\n".join(
            f"{'Usuario' if role == 'user' else 'Arquitecto Danwor'}: {text}"
            for role, text in history
        )
        directive = (directive or "").strip()
        # Con directiva (p. ej. "Explicame vos" tras un lead de catálogo) NO inyectamos el
        # grounding del inventario: el usuario ya vio esa respuesta y ahora pidió mirada
        # general. El grounding volvería a anclar el "no tenemos" y el LLM lo parrotearía.
        catalog_block = "" if directive else _catalog_grounding(question)
        directive_block = (
            f"DIRECTIVA (prioritaria)\n{'-' * 40}\n{directive}\n\n" if directive else ""
        )
        # general=True: no inyectamos el módulo activo como "fuente de verdad" --
        # es justamente el módulo cuyo contenido NO cubre el tema (por eso no hubo
        # match local); forzarlo como contexto ancla otra vez el "no" en vez de
        # dejar que el LLM responda con conocimiento general del producto.
        context_block = (
            "" if general else
            f"CONTEXTO (Marco Danwor)\n{'-' * 40}\n{context or document.source}\n\n"
        )
        user_prompt = (
            directive_block
            + (f"{catalog_block}\n\n" if catalog_block else "")
            + context_block
            + f"CONVERSACION PREVIA\n{'-' * 40}\n{conversation or '(sin mensajes previos)'}\n\n"
            f"NUEVA CONSULTA\n{'-' * 40}\n{question}\n\n"
            f"{DATA_INTEGRITY_RULE}\n\n"
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
            # Falla de proveedor conocida (red/timeout/rate limit/servidor) →
            # error tipado que el router traduce a un fragmento seguro. Un bug de
            # programación NO se mislabela como "IA no disponible": se propaga.
            err = classify_openai_error(exc)
            if err is not None:
                raise err from exc
            raise
        if not answer:
            raise SectionAnalysisError("El modelo no devolvió contenido.")
        return answer, render_llm_html(answer)


class AssistantFeedbackRepository:
    def __init__(self, root: Path = FEEDBACK_LOG_DIR) -> None:
        self.root = Path(root)

    def save(self, reply: AssistantReply, rating: str, comment: str = "", user: dict | None = None) -> Path:
        if rating not in {"up", "down"}:
            raise ValueError("Rating inválido")
        if rating == "down" and not comment.strip():
            raise ValueError("Contanos brevemente qué deberíamos mejorar.")
        self.root.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = self.root / f"feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        user = user or {}
        payload = {
            "type": "assistant_response",
            "module": reply.module,
            "topic": reply.topic,
            "action": reply.action,
            "rating": rating,
            "comment": comment.strip(),
            "question": reply.question[:300],
            "ts": now.isoformat(),
            # Correlación con activity_log (ver webapp/main.py assistant_chat, que loguea
            # los mismos reply_id/conversation_id en el evento assistant_query): permite
            # reconstruir la "historia" completa pregunta → router → feedback → Explícame
            # vos en el dashboard de actividad. Opcional: YAML viejos no lo tienen y
            # simplemente no correlacionan (se ven sueltos, como antes).
            "reply_id": reply.id,
            "conversation_id": reply.conversation_id,
            "user": str(user.get("sub") or user.get("email") or ""),
            "name": str(user.get("name") or ""),
        }
        remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
        return path
