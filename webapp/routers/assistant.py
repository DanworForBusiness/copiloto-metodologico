"""
assistant.py — Assistant + Section Analysis HTTP (feature COPILOT).

Router de las 5 rutas de interacción con el asistente: análisis de sección
(`POST /modules/{stem}/sections/analyze`), chat libre/routing
(`POST /modules/{stem}/assistant/chat`) y las 3 rutas de feedback sobre una
respuesta (`POST /assistant/responses/{reply_id}/feedback(+submit,+cancel)`).
Extraído de ``webapp/main.py`` (Fase 2J-B) como relocación pura — sin
rediseño de auth/pack/routing/feedback.

Section Analysis vive intencionalmente en este mismo router: usa
``assistant_store``, el código ya la trata como una interacción del
asistente (se loguea como ``assistant_query``, comparte conversación/reply),
y un router de una sola ruta no se justifica solo por pureza de archivo.
Esto NO vuelve a Assistant una capacidad platform/shared.

Feature COPILOT que consume capacidades compartidas desde sus dueños
explícitos (`core.content_registry`, `core.prompts`, `core.utils`,
`webapp.access`, `webapp.services.auth_service`,
`webapp.services.content_service`, `webapp.services.activity_service`,
`webapp.templating`) y el estado de proceso del Copiloto desde
`webapp.copilot_runtime.assistant_store` — SIN importar ``webapp.main``.

Los 4 servicios stateless que estas rutas necesitan
(`SectionAnalysisService`, `AssistantChatService`, `AssistantRoutingService`,
`AssistantFeedbackRepository`) se construyen acá — son config/repository
objects sin identidad compartida requerida (ver Fase 2J assessment); NO
viven en `copilot_runtime` porque ese módulo posee únicamente estado
mutable de proceso compartido (`store`, `assistant_store`).
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.content_registry import DEFAULT_PACK, resolve_pack
from core.prompts import ALL_ACTIONS, MODULE_ACTIONS
from core.utils import slugify
from webapp import access
from webapp.copilot_runtime import assistant_store
from webapp.services import auth_service
from webapp.services.activity_service import log_activity, log_verbose
from webapp.services.activity_context import SCOPE_COPILOT, AREA_ASSISTANT
from webapp.services.assistant_service import (
    AssistantChatService,
    AssistantFeedbackRepository,
    StaleAssistantReplyError,
)
from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.llm_client import LLMServiceError, log_failure
from webapp.services.content_service import load_module
from webapp.services.section_analysis import (
    MODULE_TARGET_SLUG,
    SectionAnalysisError,
    SectionAnalysisService,
    render_llm_html,
)
from webapp.templating import templates

router = APIRouter()

section_analysis_service = SectionAnalysisService()
assistant_chat_service = AssistantChatService()
assistant_routing_service = AssistantRoutingService()
assistant_feedback_repository = AssistantFeedbackRepository()


@router.post("/modules/{stem}/sections/analyze", response_class=HTMLResponse)
def analyze_section(
    request: Request,
    stem: str,
    section_slug: str = Form(""),
    action: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
    conversation_id: str = Form(""),
):
    if access.is_readonly(request):
        return HTMLResponse(
            '<p class="section-analysis-warning-copy">IA no disponible para Danwor Academy: '
            'el análisis de sección genera contenido con IA y está deshabilitado en tu perfil por ahora.</p>',
            status_code=403,
        )
    selected = resolve_pack(pack)
    user = auth_service.session_user(request) or {}
    first_name = str(user.get("first_name") or "").strip()
    if not first_name:
        first_name = str(user.get("name") or "Tú").strip().split()[0]
    try:
        document = load_module(stem, selected)
        if not conversation_id:
            conversation_id = assistant_store.start(stem, selected).id
        is_module_target = section_slug == MODULE_TARGET_SLUG
        valid_action = action in (MODULE_ACTIONS if is_module_target else ALL_ACTIONS)
        valid_target = is_module_target or any(
            section.slug == section_slug for section in document.sections
        )
        if not valid_action or not valid_target:
            raise SectionAnalysisError("La sección o acción seleccionada no es válida.")
        if not assistant_store.claim_section_analysis(
            conversation_id, stem, selected, section_slug, action,
        ):
            return HTMLResponse(
                '<p class="section-analysis-warning-copy">Ya analizaste esta acción. '
                'Elegí otra acción o sección para realizar un nuevo análisis.</p>',
                headers={
                    "HX-Retarget": "#section-analysis-warning",
                    "HX-Reswap": "innerHTML",
                    "X-Danwor-Duplicate": "true",
                },
            )
        result = section_analysis_service.analyze(document, section_slug, action, selected)
        user_message = f"{result.section_title} → {result.action}"
        reply = assistant_store.append(
            conversation_id, stem, selected, user_message, result.answer, result.html,
            topic=result.section_title, action=result.action,
        )
        error = ""
    except (FileNotFoundError, KeyError, SectionAnalysisError, LLMServiceError) as exc:
        result = None
        reply = None
        user_message = ""
        # Falla de proveedor LLM conocida → copy seguro específico + diagnóstico
        # estructurado (no exponemos detalles al browser). El resto (validación /
        # contenido faltante) conserva su mensaje de dominio.
        if isinstance(exc, LLMServiceError):
            error = exc.public_message
            log_failure("section_analysis", exc)
        else:
            error = str(exc)
    # Se loguea como assistant_query (no un evento aparte): una acción de la tab
    # "Sección" ES una interacción con el asistente (aparece en la conversación),
    # así que debe entrar a la "Historia de preguntas" y al KPI de IA igual que un
    # chat. Lleva section/action en el payload para no perder ese detalle.
    log_verbose(
        "assistant_query", user=user, scope=SCOPE_COPILOT, area=AREA_ASSISTANT,
        stem=stem, section=section_slug, action=action,
        question=(user_message or f"{action} · {section_slug}"),
        answer=(reply.answer if reply else ""),
        routed=False, route_heading="", bypass=False,
        llm=(reply is not None),
        reply_id=(reply.id if reply else ""),
        conversation_id=conversation_id,
        api_cost=bool(
            reply is not None
            and not section_analysis_service.execution_mode.lower().startswith("simul")
        ),
        error=(error or ""),
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/section_analysis_result.html",
        context={
            "result": result,
            "reply": reply,
            "user_message": user_message,
            "error": error,
            "diagnostic_mode": access.is_diagnostic(request),
            "user_first_name": first_name,
            "llm_answer": reply is not None,
        },
    )


@router.post("/modules/{stem}/assistant/chat", response_class=HTMLResponse)
def assistant_chat(
    request: Request,
    stem: str,
    question: str = Form(""),
    conversation_id: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
    active_section: str = Form(""),
    routing_bypass: bool = Form(False),
    route_module: str = Form(""),
    route_heading: str = Form(""),
    explain_prompt: str = Form(""),
):
    # Perfil solo-lectura: el asistente SÍ funciona (el router interno es determinista
    # y no consume OpenAI), pero cualquier salida que dispararía la API de OpenAI
    # (chat libre sin ruta, "Explicame vos") se reemplaza por un aviso, sin gastar tokens.
    readonly = access.is_readonly(request)
    selected = resolve_pack(pack)
    user = auth_service.session_user(request) or {}
    first_name = str(user.get("first_name") or "").strip()
    if not first_name:
        first_name = str(user.get("name") or "Tú").strip().split()[0]
    ia_blocked = False
    try:
        document = load_module(stem, selected)
        history = assistant_store.history(conversation_id, stem, selected)
        route = None if routing_bypass else assistant_routing_service.route(
            document, question, selected, active_section,
        )
        answer_document = document
        answer_context = None
        if routing_bypass and route_module:
            answer_document = load_module(route_module, selected)
            if route_heading:
                target_slug = slugify(route_heading)
                target_section = next(
                    (item for item in answer_document.sections if item.slug == target_slug),
                    None,
                )
                answer_context = target_section.markdown if target_section else None
        ia_blocked = False
        if route:
            answer = route.message
            answer_html = render_llm_html(answer)
        elif readonly:
            # El flujo requería OpenAI (sin ruta curada / "Explicame vos") → no disponible.
            ia_blocked = True
            answer = (
                "**IA no disponible para Danwor Academy.** El asistente puede orientarte "
                "con el contenido curado del marco, pero las respuestas generadas con IA "
                "están deshabilitadas en tu perfil por ahora."
            )
            answer_html = render_llm_html(answer)
        else:
            answer, answer_html = assistant_chat_service.ask(
                answer_document, question, selected, history, context=answer_context,
                directive=explain_prompt,
            )
        reply = assistant_store.append(
            conversation_id, stem, selected, question.strip(), answer, answer_html,
            topic=route_heading or "Pregunta libre",
            action="Routing" if route else ("IA bloqueada" if ia_blocked else "Asistente"),
            include_user=not routing_bypass,
        )
        error = ""
    except (FileNotFoundError, KeyError, SectionAnalysisError, LLMServiceError) as exc:
        reply = None
        route = None
        answer_html = ""
        if isinstance(exc, LLMServiceError):
            error = exc.public_message
            log_failure("assistant_chat", exc)
        else:
            error = str(exc)
    # Telemetría fina (VERBOSE): qué preguntaron y cómo resolvió el copiloto.
    # La señal más rica para el ejercicio "romper/buscar fallas".
    log_verbose(
        "assistant_query", user=user, scope=SCOPE_COPILOT, area=AREA_ASSISTANT,
        stem=stem, section=(active_section or ""),
        question=question.strip(),
        # Texto de la respuesta (router o LLM) para poder verla en la "historia" del
        # dashboard, no solo saber QUE se contestó. reply.answer siempre existe cuando
        # reply no es None (reply=None solo en el except de arriba).
        answer=(reply.answer if reply else ""),
        routed=(route is not None),
        route_module=(getattr(route, "module", "") or route_module or "") if route else route_module,
        route_heading=(getattr(route, "explain_heading", "") or getattr(route, "heading", "") or route_heading) if route else route_heading,
        intent=getattr(route, "intent", "") if route else "",
        bypass=bool(routing_bypass),
        readonly=bool(readonly),
        ia_blocked=bool(ia_blocked),
        llm=(route is None and reply is not None and not readonly),
        error=(error or ""),
        # Correlación con feedback_log (ver AssistantFeedbackRepository.save): permite
        # reconstruir en el dashboard de actividad la historia completa de una conversación
        # (turno router → turno "Explícame vos" → feedback 👍/👎 con comentario).
        reply_id=(reply.id if reply else ""),
        conversation_id=conversation_id,
        # Distingue consumo REAL de OpenAI (con costo) del router/simulación: "llm" da
        # True en ambos casos (ver AssistantChatService.ask, modo simulación no llama a
        # la API), así que acá se resta explícitamente ese modo para el KPI de costo.
        api_cost=bool(
            route is None and reply is not None and not readonly
            and not assistant_chat_service.execution_mode.lower().startswith("simul")
        ),
    )
    return templates.TemplateResponse(
        request=request,
        name="partials/section_analysis_result.html",
        context={
            "result": None,
            "reply": reply,
            "user_message": question.strip(),
            "answer_html": answer_html,
            "error": error,
            "diagnostic_mode": access.is_diagnostic(request),
            "user_first_name": first_name,
            "route": route,
            "stem": stem,
            "pack": selected,
            "show_user_message": not routing_bypass,
            "llm_answer": route is None and reply is not None and not readonly,
        },
    )


def _assistant_feedback_template(request, name, reply, **context):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"reply": reply, **context},
    )


def _log_assistant_feedback_reaction(reply, rating: str, user: dict | None) -> None:
    """USAGE METRICS — REACCIONES (owner: cobertura completa de Copiloto, ver
    AUDIT): MISMO criterio que module.feedback/section.feedback/academy.feedback
    — evento del pipeline de Activity, `log_activity` (NUNCA `log_verbose`:
    una reacción es una señal de negocio, no telemetría fina — MISMO criterio
    que las otras 8 familias de feedback, todas siempre-on). `reply_id` viaja
    en el payload, MISMO campo que `assistant_query` ya usa para correlacionar
    con `AssistantFeedbackRepository.save` (ver ese comentario) — reusable
    como clave de deduplicación de un futuro backfill histórico, nunca una
    clave nueva. Best-effort: nunca puede hacer que `assistant_feedback(_submit)`
    falle después de que el feedback YA se guardó."""
    try:
        log_activity(
            "assistant.feedback", user=user, scope=SCOPE_COPILOT, area=AREA_ASSISTANT,
            module=reply.module, reply_id=reply.id, conversation_id=reply.conversation_id,
            feedback_sentiment=("positive" if rating == "up" else "negative"),
        )
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


@router.post("/assistant/responses/{reply_id}/feedback", response_class=HTMLResponse)
def assistant_feedback(request: Request, reply_id: str, rating: str = Form("")):
    try:
        reply = assistant_store.reply_for_feedback(reply_id)
    except KeyError as exc:
        raise HTTPException(410, "La respuesta ya no está disponible") from exc
    except StaleAssistantReplyError as exc:
        raise HTTPException(409, "Solo podés calificar la respuesta más reciente") from exc
    if rating == "down":
        return _assistant_feedback_template(
            request, "partials/assistant_feedback_form.html", reply, error="",
        )
    user = auth_service.session_user(request)
    try:
        assistant_feedback_repository.save(reply, "up", user=user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _log_assistant_feedback_reaction(reply, "up", user)
    return _assistant_feedback_template(
        request, "partials/assistant_feedback_thanks.html", reply,
    )


@router.post("/assistant/responses/{reply_id}/feedback/submit", response_class=HTMLResponse)
def assistant_feedback_submit(
    request: Request,
    reply_id: str,
    comment: str = Form(""),
):
    user = auth_service.session_user(request)
    try:
        reply = assistant_store.reply_for_feedback(reply_id)
        assistant_feedback_repository.save(reply, "down", comment, user=user)
    except KeyError as exc:
        raise HTTPException(410, "La respuesta ya no está disponible") from exc
    except StaleAssistantReplyError as exc:
        raise HTTPException(409, "Solo podés calificar la respuesta más reciente") from exc
    except ValueError as exc:
        return _assistant_feedback_template(
            request, "partials/assistant_feedback_form.html", reply, error=str(exc),
        )
    _log_assistant_feedback_reaction(reply, "down", user)
    return _assistant_feedback_template(
        request, "partials/assistant_feedback_thanks.html", reply,
    )


@router.post("/assistant/responses/{reply_id}/feedback/cancel", response_class=HTMLResponse)
def assistant_feedback_cancel(request: Request, reply_id: str):
    try:
        reply = assistant_store.reply_for_feedback(reply_id)
    except KeyError as exc:
        raise HTTPException(410, "La respuesta ya no está disponible") from exc
    except StaleAssistantReplyError as exc:
        raise HTTPException(409, "Solo podés calificar la respuesta más reciente") from exc
    return _assistant_feedback_template(
        request, "partials/assistant_feedback.html", reply,
    )
