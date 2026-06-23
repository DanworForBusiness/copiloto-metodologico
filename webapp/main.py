from pathlib import Path
import json
import re
import unicodedata

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from core.content_registry import (
    available_content_packs,
    content_pack_label,
    content_pack_short_label,
    resolve_pack_file,
    roots_for_kind,
)
from config import (
    APP_OWNER, AUTH_DEFAULT_DOMAIN, AZURE_CLIENT_ID, AZURE_TENANT_ID, DIAGNOSTIC_MODE,
    SESSION_MAX_AGE, SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE, ia_enabled_for,
)
from core.prompts import ALL_ACTIONS, get_loading_message
from core.utils import slugify
from webapp.services import auth_service
from webapp.services.content_service import list_appendices, list_modules, load_module
from webapp.services.case_library import CaseLibraryRepository
from webapp.services.assistant_service import (
    AssistantChatService,
    AssistantConversationStore,
    AssistantFeedbackRepository,
    StaleAssistantReplyError,
)
from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.section_analysis import (
    ACTION_DESCRIPTIONS,
    SectionAnalysisError,
    SectionAnalysisService,
    render_llm_html,
)
from webapp.services.wizard_service import WizardStore

DEFAULT_PACK = "datasphere_mvp"
WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.globals["static_version"] = str(max(
    path.stat().st_mtime_ns for path in (WEB_DIR / "static").iterdir() if path.is_file()
))
store = WizardStore(case_repository=CaseLibraryRepository())
section_analysis_service = SectionAnalysisService()
assistant_chat_service = AssistantChatService()
assistant_routing_service = AssistantRoutingService()
assistant_store = AssistantConversationStore()
assistant_feedback_repository = AssistantFeedbackRepository()
app = FastAPI(title="Copiloto Metodológico Danwor Beta", version="0.2.0")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


def _pack(value: str) -> str:
    return value if value in available_content_packs() else DEFAULT_PACK


def _owner_emails() -> set[str]:
    """APP_OWNER → set de mails. Acepta CSV y alias sin dominio (joconnor →
    joconnor@danwor.com), igual que ALLOWED_USERS."""
    domain = (AUTH_DEFAULT_DOMAIN or "danwor.com").strip().lower().lstrip("@")
    owners = set()
    for raw in (APP_OWNER or "").split(","):
        u = raw.strip().lower()
        if u:
            owners.add(u if "@" in u else f"{u}@{domain}")
    return owners


def _is_owner(request: Request) -> bool:
    """True si el usuario logueado es el dueño de la app (config APP_OWNER)."""
    owners = _owner_emails()
    if not owners:
        return False
    user = auth_service.session_user(request)
    return bool(user) and (str(user.get("sub") or "").strip().lower() in owners)


def _diagnostic(request: Request) -> bool:
    """Modo diagnóstico: solo el dueño (APP_OWNER) puede activarlo por cookie.

    Habilita el selector de marco metodológico y la pestaña/grupo de gestión.
    DANWOR_DIAGNOSTIC_MODE lo fuerza por entorno (dev). Para usuarios no-owner
    queda siempre off, aunque tengan la cookie seteada a mano.
    """
    if DIAGNOSTIC_MODE:
        return True
    return _is_owner(request) and request.cookies.get("dw-diagnostic") == "on"


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(WEB_DIR / "static" / "favicon.ico")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok", "architecture": "fastapi-ssr-htmx"}


# ── Autenticación Microsoft (Entra ID) ─────────────────────────────────────────
class _MsTokenBody(BaseModel):
    id_token: str


@app.get("/api/auth/microsoft/config", include_in_schema=False)
def microsoft_config():
    return {
        "enabled": auth_service.microsoft_configured(),
        "tenant_id": AZURE_TENANT_ID,
        "client_id": AZURE_CLIENT_ID,
    }


@app.post("/api/auth/microsoft/token", include_in_schema=False)
def microsoft_token(body: _MsTokenBody):
    try:
        claims = auth_service.verify_ms_id_token(body.id_token)
        user = auth_service.user_from_claims(claims)
    except auth_service.AuthError as exc:
        detail = {"message": str(exc)}
        if exc.denied_user:
            detail = {"code": "microsoft_user_not_authorized", "denied_user": exc.denied_user, "message": str(exc)}
        return JSONResponse({"detail": detail}, status_code=403 if exc.denied_user else 401)
    except Exception as exc:  # noqa: BLE001 — fallo de red/JWKS u otro
        return JSONResponse({"detail": {"message": f"No se pudo validar el login: {exc}"}}, status_code=502)

    response = JSONResponse({"ok": True, "first_name": user["first_name"], "name": user["name"]})
    response.set_cookie(
        auth_service.COOKIE_NAME, auth_service.create_session(user),
        max_age=SESSION_MAX_AGE, httponly=True, path="/",
        samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


@app.get("/api/auth/me", include_in_schema=False)
def auth_me(request: Request):
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return {"name": user.get("name"), "first_name": user.get("first_name"), "email": user.get("sub")}


@app.post("/api/auth/logout", include_in_schema=False)
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        auth_service.COOKIE_NAME, path="/",
        samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


@app.get("/brand/dancing/{pack_name}", include_in_schema=False)
def dancing_logo(pack_name: str):
    selected = _pack(pack_name)
    path = resolve_pack_file("assets", "danwor-animated-dancing.html", selected)
    if not path.exists():
        raise HTTPException(404, "Logo no encontrado")
    source = path.read_text(encoding="utf-8", errors="ignore")
    # El asset original fue diseñado para fondo claro. Esta variante conserva
    # animaciones y dorados, pero usa la paleta legible del shell oscuro.
    dark_shell_colors = {
        "#363636": "#e4e4ec",
        "#1a1a1a": "#e4e4ec",
        "#5e6166": "#8b8b96",
        "#d0d0d0": "rgba(226,232,245,0.18)",
        "0x36": "0xe4",
    }
    for original, replacement in dark_shell_colors.items():
        source = source.replace(original, replacement)
    return HTMLResponse(source)


@app.get("/pack-assets/{pack_name}/{asset_path:path}", include_in_schema=False)
def pack_asset(request: Request, pack_name: str, asset_path: str):
    selected = _pack(pack_name)
    relative = Path(asset_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "Ruta de asset inválida")
    path = resolve_pack_file("assets", relative, selected).resolve()
    allowed_roots = [root.resolve() for root in roots_for_kind("assets", selected)]
    if not path.exists() or not any(path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(404, "Asset no encontrado")
    if path.suffix.lower() == ".html":
        theme = request.cookies.get("dw-content-theme", "light")
        props = {"theme": theme if theme in ("light", "dark") else "light"}
        for key, value in request.query_params.items():
            normalized = key.lower().replace("-", "_")
            props[normalized] = value
            bits = [part for part in re.split(r"[_-]+", normalized) if part]
            if bits:
                props[bits[0] + "".join(part.title() for part in bits[1:])] = value
        payload = json.dumps(props, ensure_ascii=False).replace("</", "<\\/")
        # Soporte de tema común a TODOS los embeds: en modo oscuro vuelve
        # transparente el fondo del asset (mata el recuadro blanco; se ve el
        # canvas oscuro detrás) y escucha el cambio en vivo del copiloto.
        theme_support = (
            '<style>html[data-dw-theme="dark"],html[data-dw-theme="dark"] body'
            '{background:transparent!important}'
            # Scrollbars sutiles en dark (thumb claro translúcido, track transparente).
            'html[data-dw-theme="dark"]{scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.18) transparent}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar,html[data-dw-theme="dark"] ::-webkit-scrollbar'
            '{width:9px;height:9px}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar-track,html[data-dw-theme="dark"] ::-webkit-scrollbar-track'
            '{background:transparent}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar-thumb,html[data-dw-theme="dark"] ::-webkit-scrollbar-thumb'
            '{background:rgba(255,255,255,.16);border-radius:9px;border:3px solid transparent;background-clip:padding-box}'
            'html[data-dw-theme="dark"]::-webkit-scrollbar-thumb:hover,html[data-dw-theme="dark"] ::-webkit-scrollbar-thumb:hover'
            '{background:rgba(255,255,255,.30);background-clip:padding-box}'
            # Mismo scrollbar sutil en claro (thumb oscuro translúcido, como los paneles).
            'html[data-dw-theme="light"]{scrollbar-width:thin;scrollbar-color:rgba(20,22,28,.18) transparent}'
            'html[data-dw-theme="light"]::-webkit-scrollbar,html[data-dw-theme="light"] ::-webkit-scrollbar'
            '{width:9px;height:9px}'
            'html[data-dw-theme="light"]::-webkit-scrollbar-track,html[data-dw-theme="light"] ::-webkit-scrollbar-track'
            '{background:transparent}'
            'html[data-dw-theme="light"]::-webkit-scrollbar-thumb,html[data-dw-theme="light"] ::-webkit-scrollbar-thumb'
            '{background:rgba(20,22,28,.16);border-radius:9px;border:3px solid transparent;background-clip:padding-box}'
            'html[data-dw-theme="light"]::-webkit-scrollbar-thumb:hover,html[data-dw-theme="light"] ::-webkit-scrollbar-thumb:hover'
            '{background:rgba(20,22,28,.30);background-clip:padding-box}</style>'
            "<script>(function(){function a(t){document.documentElement"
            ".setAttribute('data-dw-theme',t||'light');}"
            "a((window.DANWOR_EMBED_PROPS||{}).theme);"
            "window.addEventListener('message',function(e){"
            "if(e.data&&e.data.type==='danwor-theme')a(e.data.theme);});})();</script>"
        )
        injection = f"<script>window.DANWOR_EMBED_PROPS = {payload};</script>{theme_support}"
        source = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"</head>", source, re.I):
            source = re.sub(r"</head>", injection + "\n</head>", source, count=1, flags=re.I)
        else:
            source = injection + "\n" + source
        return HTMLResponse(source)
    return FileResponse(path)


@app.get("/pack-resources/{pack_name}/{kind}/{resource_path:path}", include_in_schema=False)
def pack_resource(pack_name: str, kind: str, resource_path: str):
    selected = _pack(pack_name)
    if kind not in {"assets", "templates"}:
        raise HTTPException(404, "Tipo de recurso no encontrado")
    relative = Path(resource_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "Ruta de recurso inválida")
    path = resolve_pack_file(kind, relative, selected).resolve()
    allowed_roots = [root.resolve() for root in roots_for_kind(kind, selected)]
    if not path.exists() or not any(path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(404, "Recurso no encontrado")
    return FileResponse(path)


def _home_url(pack: str) -> str:
    """URL del primer módulo del pack (entrada a la app tras el login)."""
    modules = list_modules(pack)
    if not modules:
        raise HTTPException(503, "No hay módulos configurados")
    module = next((item for item in modules if item.number == "01"), modules[0])
    return f"/modules/{module.stem}?pack={pack}"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(request: Request, pack: str = Query(DEFAULT_PACK)):
    selected = _pack(pack)
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"pack": selected, "home_url": _home_url(selected)},
    )


@app.get("/login", include_in_schema=False)
def login(pack: str = Query(DEFAULT_PACK)):
    # El acceso vive en la misma página que el landing (misma base, como el sitio
    # de marca). /login baja directo a la sección de acceso.
    return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)


@app.get("/modules/{stem}", response_class=HTMLResponse)
def module_page(request: Request, stem: str, pack: str = Query(DEFAULT_PACK)):
    diagnostic = _diagnostic(request)
    # Fuera de modo diagnóstico, el marco es siempre el productivo (Datasphere).
    selected = _pack(pack) if diagnostic else DEFAULT_PACK
    user = auth_service.session_user(request)
    if not user:  # sin sesión válida → al landing/acceso
        return RedirectResponse(f"/?pack={selected}#acceso", status_code=307)
    modules = list_modules(selected)
    appendices = list_appendices(selected)
    try:
        document = load_module(stem, selected)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Módulo no encontrado") from exc

    # Nav combinado: módulos principales + apéndices técnicos (módulo final).
    nav_items = (*modules, *appendices)
    is_appendix = any(item.stem == stem for item in appendices)
    ia_enabled = ia_enabled_for(stem)
    current_index = next((i for i, item in enumerate(nav_items) if item.stem == stem), None)
    previous_module = nav_items[current_index - 1] if current_index else None
    next_module = (
        nav_items[current_index + 1]
        if current_index is not None and current_index + 1 < len(nav_items)
        else None
    )
    trees = store.trees_for_module(stem, selected)
    run = store.start(trees[0]) if len(trees) == 1 else None
    conversation = assistant_store.start(stem, selected)
    packs = [
        {"name": name, "label": content_pack_short_label(name), "description": content_pack_label(name), "home": _home_url(name)}
        for name in available_content_packs()
    ]
    return templates.TemplateResponse(
        request=request,
        name="module.html",
        context={
            "modules": modules,
            "appendices": appendices,
            "ia_enabled": ia_enabled,
            "is_appendix": is_appendix,
            "diagnostic": diagnostic,
            "is_owner": _is_owner(request),
            # El panel derecho se muestra si el módulo tiene IA (grupo Decisión) o
            # si está el modo diagnóstico activo (grupo Gestión).
            "show_panel": ia_enabled or diagnostic,
            "document": document,
            "trees": trees,
            "run": run,
            "node": run.node if run else None,
            "leaf": run.leaf if run else None,
            "selected_tree_id": run.tree.get("id") if run else "",
            "pack": selected,
            "packs": packs,
            "pack_label": content_pack_short_label(selected),
            "previous_module": previous_module,
            "next_module": next_module,
            "section_actions": ALL_ACTIONS,
            "section_action_descriptions": ACTION_DESCRIPTIONS,
            "section_loading_messages": {
                action: get_loading_message(action, False) for action in ALL_ACTIONS
            },
            "user": user,
            "conversation_id": conversation.id,
        },
    )


@app.post("/modules/{stem}/sections/analyze", response_class=HTMLResponse)
def analyze_section(
    request: Request,
    stem: str,
    section_slug: str = Form(""),
    action: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
    conversation_id: str = Form(""),
):
    selected = _pack(pack)
    user = auth_service.session_user(request) or {}
    first_name = str(user.get("first_name") or "").strip()
    if not first_name:
        first_name = str(user.get("name") or "Tú").strip().split()[0]
    try:
        document = load_module(stem, selected)
        if not conversation_id:
            conversation_id = assistant_store.start(stem, selected).id
        if action not in ALL_ACTIONS or not any(
            section.slug == section_slug for section in document.sections
        ):
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
    except (FileNotFoundError, KeyError, SectionAnalysisError) as exc:
        result = None
        reply = None
        user_message = ""
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="partials/section_analysis_result.html",
        context={
            "result": result,
            "reply": reply,
            "user_message": user_message,
            "error": error,
            "diagnostic_mode": _diagnostic(request),
            "user_first_name": first_name,
        },
    )


@app.post("/modules/{stem}/assistant/chat", response_class=HTMLResponse)
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
):
    selected = _pack(pack)
    user = auth_service.session_user(request) or {}
    first_name = str(user.get("first_name") or "").strip()
    if not first_name:
        first_name = str(user.get("name") or "Tú").strip().split()[0]
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
        if route:
            answer = route.message
            answer_html = render_llm_html(answer)
        else:
            answer, answer_html = assistant_chat_service.ask(
                answer_document, question, selected, history, context=answer_context,
            )
        reply = assistant_store.append(
            conversation_id, stem, selected, question.strip(), answer, answer_html,
            topic=route_heading or "Pregunta libre", action="Routing" if route else "Asistente",
            include_user=not routing_bypass,
        )
        error = ""
    except (FileNotFoundError, KeyError, SectionAnalysisError) as exc:
        reply = None
        route = None
        answer_html = ""
        error = str(exc)
    return templates.TemplateResponse(
        request=request,
        name="partials/section_analysis_result.html",
        context={
            "result": None,
            "reply": reply,
            "user_message": question.strip(),
            "answer_html": answer_html,
            "error": error,
            "diagnostic_mode": _diagnostic(request),
            "user_first_name": first_name,
            "route": route,
            "stem": stem,
            "pack": selected,
            "show_user_message": not routing_bypass,
        },
    )


def _assistant_feedback_template(request, name, reply, **context):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"reply": reply, **context},
    )


@app.post("/assistant/responses/{reply_id}/feedback", response_class=HTMLResponse)
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
    try:
        assistant_feedback_repository.save(reply, "up")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _assistant_feedback_template(
        request, "partials/assistant_feedback_thanks.html", reply,
    )


@app.post("/assistant/responses/{reply_id}/feedback/submit", response_class=HTMLResponse)
def assistant_feedback_submit(
    request: Request,
    reply_id: str,
    comment: str = Form(""),
):
    try:
        reply = assistant_store.reply_for_feedback(reply_id)
        assistant_feedback_repository.save(reply, "down", comment)
    except KeyError as exc:
        raise HTTPException(410, "La respuesta ya no está disponible") from exc
    except StaleAssistantReplyError as exc:
        raise HTTPException(409, "Solo podés calificar la respuesta más reciente") from exc
    except ValueError as exc:
        return _assistant_feedback_template(
            request, "partials/assistant_feedback_form.html", reply, error=str(exc),
        )
    return _assistant_feedback_template(
        request, "partials/assistant_feedback_thanks.html", reply,
    )


@app.post("/assistant/responses/{reply_id}/feedback/cancel", response_class=HTMLResponse)
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


def _norm_tokens(text: str) -> list[str]:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9]+", ascii_text) if t]


def _resolve_tree(trees: tuple, tree_id: str, tree_title: str) -> dict | None:
    """Resuelve un árbol por id exacto; si no, por título (exacto, luego por
    subconjunto de tokens — p. ej. el botón 'Estrategia de Integración (federación
    vs persistencia)' matchea el árbol 'Federación vs Persistencia')."""
    if tree_id:
        tree = next((t for t in trees if str(t.get("id")) == tree_id), None)
        if tree:
            return tree
    title = (tree_title or "").strip()
    if not title:
        return None
    low = title.lower()
    for t in trees:
        if str(t.get("title") or "").strip().lower() == low:
            return t
    button_tokens = set(_norm_tokens(title))
    best, best_len = None, 0
    for t in trees:
        tree_tokens = _norm_tokens(str(t.get("title") or ""))
        if tree_tokens and len(tree_tokens) > best_len and all(tok in button_tokens for tok in tree_tokens):
            best, best_len = t, len(tree_tokens)
    return best


@app.post("/modules/{stem}/trees/start", response_class=HTMLResponse)
def start_tree(
    request: Request,
    stem: str,
    tree_id: str = Form(""),
    tree_title: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = _pack(pack)
    trees = store.trees_for_module(stem, selected)
    tree = _resolve_tree(trees, tree_id, tree_title)
    if tree is None:
        raise HTTPException(404, "Árbol no encontrado")
    run = store.start(tree)
    return templates.TemplateResponse(
        request=request,
        name="partials/tree_tab.html",
        context={
            "trees": trees,
            "run": run,
            "node": run.node,
            "leaf": run.leaf,
            "selected_tree_id": tree.get("id"),
            "document": load_module(stem, selected),
            "pack": selected,
        },
    )


def _fragment(request, run):
    return templates.TemplateResponse(
        request=request,
        name="partials/wizard.html",
        context={"run": run, "node": run.node, "leaf": run.leaf},
    )


@app.post("/wizards/{run_id}/answer", response_class=HTMLResponse)
def answer(request: Request, run_id: str, answer: str = Form("")):
    try:
        return _fragment(request, store.answer(run_id, answer))
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc


@app.post("/wizards/{run_id}/back", response_class=HTMLResponse)
def back(request: Request, run_id: str):
    try:
        return _fragment(request, store.back(run_id))
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc


@app.post("/wizards/{run_id}/restart", response_class=HTMLResponse)
def restart(request: Request, run_id: str):
    try:
        return _fragment(request, store.restart(run_id))
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc


def _thanks(request, run):
    return templates.TemplateResponse(
        request=request,
        name="partials/tree_feedback.html",
        context={"run": run, "diagnostic_mode": _diagnostic(request)},
    )


def _feedback_form(request, run, rating, error=""):
    return templates.TemplateResponse(
        request=request, name="partials/tree_feedback_form.html",
        context={"run": run, "rating": rating, "error": error},
    )


@app.post("/wizards/{run_id}/feedback", response_class=HTMLResponse)
def tree_feedback(request: Request, run_id: str, rating: str = Form("")):
    try:
        run = store.get(run_id)
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    if rating == "down":
        return _feedback_form(request, run, "down")          # 👎 abre el formulario
    try:
        run = store.submit_feedback(run_id, rating="up")      # 👍 finaliza directo
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _thanks(request, run)


@app.post("/wizards/{run_id}/feedback/submit", response_class=HTMLResponse)
def tree_feedback_submit(
    request: Request, run_id: str,
    rating: str = Form("down"), outcome: str = Form(""), context: str = Form(""),
    detail: str = Form(""), lesson: str = Form(""),
):
    try:
        run = store.submit_feedback(
            run_id, rating=rating, outcome=outcome, context=context, detail=detail, lesson=lesson,
        )
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    except ValueError as exc:                                  # falta lección → re-mostrar form con error
        return _feedback_form(request, store.get(run_id), rating, str(exc))
    return _thanks(request, run)


@app.post("/wizards/{run_id}/feedback/cancel", response_class=HTMLResponse)
def tree_feedback_cancel(request: Request, run_id: str):
    try:
        run = store.get(run_id)
    except KeyError as exc:
        raise HTTPException(410, str(exc)) from exc
    return _thanks(request, run)


if __name__ == "__main__":
    # `python -m webapp.main` levanta en 8501, el puerto cuyo redirect URI
    # (http://localhost:8501/) ya está registrado en Azure para el login MSAL.
    import uvicorn

    uvicorn.run("webapp.main:app", host="127.0.0.1", port=8501, reload=True)
