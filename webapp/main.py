from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from uuid import uuid4
import json
import re
import unicodedata
import yaml

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
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
    FEEDBACK_LOG_DIR,
    APP_OWNER, AUTH_DEFAULT_DOMAIN, AZURE_CLIENT_ID, AZURE_TENANT_ID, DESAFIO_CLOSE_URL,
    DIAGNOSTIC_MODE,
    SESSION_MAX_AGE, SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE, ia_enabled_for,
)
from core.prompts import (
    ALL_ACTIONS,
    MODULE_ACTIONS,
    MODULE_ACTION_DESCRIPTIONS,
    get_loading_message,
)
from core.utils import slugify
from webapp.services import auth_service
from webapp.services.activity_service import log_activity
from webapp.services.content_service import (
    academy_asset_for,
    list_academy,
    list_appendices,
    list_modules,
    load_module,
    split_front_matter,
)
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
    MODULE_TARGET_SLUG,
    SectionAnalysisError,
    SectionAnalysisService,
    render_llm_html,
)
from webapp.services.wizard_service import WizardStore
from webapp.services import admin_tools

DEFAULT_PACK = "datasphere_core"
WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _display_diagnostic_title(title: str) -> str:
    value = str(title or "")
    for prefix in ("Diagn\u00f3stico \u00b7 ", "Diagn\u00f3stico del ", "Diagn\u00f3stico de "):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value

templates.env.filters["diagnostic_title"] = _display_diagnostic_title
class _StaticVersion:
    """Cache-busting de estáticos: se recalcula en cada render (mtime actual de
    /static). Así, tras editar un .css/.js, un simple refresh trae la versión
    nueva sin reiniciar el server (antes se fijaba al importar el módulo → los
    cambios locales quedaban cacheados con la URL vieja)."""

    def __str__(self) -> str:
        try:
            return str(max(
                path.stat().st_mtime_ns
                for path in (WEB_DIR / "static").iterdir() if path.is_file()
            ))
        except ValueError:
            return "0"


templates.env.globals["static_version"] = _StaticVersion()
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


def _readonly(request: Request) -> bool:
    """True si el usuario logueado es perfil SOLO LECTURA (ACADEMY_USERS): navega y
    lee todo el contenido, pero con la IA apagada (sin asistente ni análisis de
    sección → sin consumo de tokens). Se deriva del email en cada request, así que
    mover un usuario entre listas surte efecto sin necesidad de re-login."""
    user = auth_service.session_user(request)
    if not user:
        return False
    return auth_service.user_tier(str(user.get("sub") or "")) == "academy"


def _diagnostic(request: Request) -> bool:
    """Modo diagnóstico: solo el dueño (APP_OWNER) puede activarlo por cookie.

    Habilita el selector de marco metodológico y la pestaña/grupo de gestión.
    DANWOR_DIAGNOSTIC_MODE lo fuerza por entorno (dev). Para usuarios no-owner
    queda siempre off, aunque tengan la cookie seteada a mano.
    """
    if DIAGNOSTIC_MODE:
        return True
    return _is_owner(request) and request.cookies.get("dw-diagnostic") == "on"



def _academy_feedback_html(stem: str, pack: str, *, submitted: bool = False, rating: str = "", form: bool = False, error: str = "") -> str:
    """Fragmento autocontenido para el iframe de Danwor Academy."""
    safe_stem = quote(stem, safe="")
    safe_pack = quote(pack, safe="")
    if submitted:
        icon = "&#128077;" if rating == "up" else "&#128078;"
        return (
            '<section id="academy-feedback" class="dw-academy-feedback is-thanks is-visible">'
            '<div class="dw-af-inner">'
            '<p class="dw-af-eyebrow"><span class="dw-af-dot"></span>Gracias por tu recorrido</p>'
            f'<p class="dw-af-prompt"><span class="dw-af-thanks-icon">{icon}</span> ¡Gracias!</p>'
            '<p class="dw-af-note">Registramos tu opinión sobre este contenido.</p>'
            '</div></section>'
        )
    if form:
        error_html = f'<p class="dw-af-error">{error}</p>' if error else ""
        return (
            '<section id="academy-feedback" class="dw-academy-feedback is-form is-visible">'
            '<div class="dw-af-inner">'
            '<p class="dw-af-eyebrow"><span class="dw-af-dot"></span>Contanos un poco más</p>'
            '<p class="dw-af-prompt">¿Qué faltó o qué mejorarías?</p>'
            '<form data-academy-feedback-form method="post" '
            f'action="/academy/{safe_stem}/feedback/submit">'
            f'<input type="hidden" name="pack" value="{safe_pack}">'
            '<input type="hidden" name="rating" value="down">'
            f'{error_html}'
            '<textarea name="comment" rows="3" placeholder="Ej.: faltó contexto, un ejemplo práctico, más claridad o profundidad..." required></textarea>'
            '<div class="dw-af-actions">'
            f'<button type="button" class="dw-af-ghost" data-academy-feedback-cancel data-url="/academy/{safe_stem}/feedback/cancel" data-pack="{safe_pack}">Cancelar</button>'
            '<button type="submit" class="dw-af-primary">Enviar</button>'
            '</div></form></div></section>'
        )
    return (
        '<section id="academy-feedback" class="dw-academy-feedback">'
        '<div class="dw-af-inner">'
        '<p class="dw-af-eyebrow"><span class="dw-af-dot"></span>Fin del recorrido</p>'
        '<p class="dw-af-prompt">¿Qué te pareció este contenido?</p>'
        '<form data-academy-feedback-form method="post" '
        f'action="/academy/{safe_stem}/feedback">'
        f'<input type="hidden" name="pack" value="{safe_pack}">'
        '<div class="dw-af-reactions">'
        '<button type="submit" name="rating" value="up" class="dw-af-react" aria-label="Me sirvió">'
        '<span class="dw-af-emoji">&#128077;</span><span class="dw-af-label">Me sirvió</span></button>'
        '<button type="submit" name="rating" value="down" class="dw-af-react" aria-label="Puede mejorar">'
        '<span class="dw-af-emoji">&#128078;</span><span class="dw-af-label">Puede mejorar</span></button>'
        '</div></form></div></section>'
    )


def _academy_feedback_assets(stem: str, pack: str) -> str:
    initial = _academy_feedback_html(stem, pack).replace("</", "<\\/")
    return """
<style>
/* Cierre del recorrido: beat final en el lenguaje de Academy (usa las variables de
   dw-academy.css → hereda tema claro/oscuro y branding solo). Aparece con reveal al
   llegar al final, en vez de una card pegada abajo. */
.dw-academy-feedback { border-top: 1px solid var(--line); margin-top: clamp(3rem,8vw,6rem); padding: clamp(3rem,7vw,5rem) 1.5rem clamp(4rem,9vw,6rem); text-align: center; opacity: 0; transform: translateY(22px); transition: opacity .7s var(--ease-out, ease), transform .7s var(--ease-out, ease); }
.dw-academy-feedback.is-visible { opacity: 1; transform: none; }
.dw-af-inner { max-width: 620px; margin: 0 auto; }
.dw-af-eyebrow { font-family: var(--mono); font-size: .72rem; letter-spacing: .14em; text-transform: uppercase; color: var(--gold); display: inline-flex; align-items: center; gap: .5rem; margin: 0 0 1rem; }
.dw-af-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--gold); box-shadow: 0 0 12px var(--gold-glow); }
.dw-af-prompt { font-family: var(--sans); font-size: clamp(1.35rem,2.4vw,1.95rem); font-weight: 800; letter-spacing: -.01em; line-height: 1.15; color: var(--ink); margin: 0 0 1.9rem; }
.dw-af-thanks-icon { font-size: 1.5rem; }
.dw-af-note { font-family: var(--sans); font-size: .92rem; color: var(--ink-soft); margin: .3rem 0 0; }
.dw-af-reactions { display: flex; justify-content: center; gap: 1rem; }
.dw-af-react { display: flex; flex-direction: column; align-items: center; gap: .55rem; min-width: 138px; padding: 1.25rem 1.5rem; border: 1px solid var(--line); border-radius: var(--radius, 14px); background: var(--bg-card); color: var(--ink-soft); font-family: var(--sans); cursor: pointer; transition: border-color .2s, transform .2s, color .2s; }
.dw-af-react:hover { border-color: var(--gold); transform: translateY(-3px); color: var(--ink); }
.dw-af-emoji { font-size: 1.7rem; line-height: 1; }
.dw-af-label { font-size: .82rem; font-weight: 650; }
.dw-academy-feedback.is-form textarea { width: 100%; min-height: 92px; padding: .8rem .9rem; border: 1px solid var(--line); border-radius: 12px; background: var(--bg-card); color: var(--ink); font: 500 .95rem/1.5 var(--sans); resize: vertical; box-sizing: border-box; }
.dw-academy-feedback.is-form textarea:focus { outline: none; border-color: var(--gold); box-shadow: 0 0 0 3px var(--gold-dim); }
.dw-af-actions { display: flex; justify-content: center; gap: .7rem; margin-top: 1rem; }
.dw-af-actions button { min-height: 40px; padding: .55rem 1.4rem; border-radius: 999px; font: 650 .85rem var(--sans); cursor: pointer; }
.dw-af-ghost { border: 1px solid var(--line); background: transparent; color: var(--ink-soft); }
.dw-af-ghost:hover { color: var(--ink); border-color: var(--ink-soft); }
.dw-af-primary { border: 1px solid var(--gold); background: var(--gold); color: #1a1400; }
.dw-af-primary:hover { filter: brightness(1.06); }
.dw-af-error { margin: 0 0 .7rem; padding: .5rem .7rem; border-left: 2px solid #c95d5d; background: rgba(201,93,93,.10); color: #d98a8a; font: 500 .85rem var(--sans); border-radius: 4px; }
@media (max-width: 560px) { .dw-af-reactions { flex-direction: column; align-items: stretch; } .dw-af-react { flex-direction: row; justify-content: center; gap: .7rem; } }
</style>
<script>
(function() {
  function observe(node) {
    if (!node || node.classList.contains('is-visible')) return;
    if (!('IntersectionObserver' in window)) { node.classList.add('is-visible'); return; }
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) { if (e.isIntersecting) { e.target.classList.add('is-visible'); io.unobserve(e.target); } });
    }, { threshold: 0.2 });
    io.observe(node);
  }
  function wireRail(fb) {
    // Suma un stop final al rail ("¿Qué te pareció?") para que el lector VEA que
    // hay un tramo más después del último bullet y scrollee hasta el cierre.
    var rail = document.getElementById('rail');
    if (!fb || !rail) return;
    if (!rail.querySelector('[data-af-rail]')) {
      var anchor = document.createElement('a');
      anchor.href = '#academy-feedback';
      anchor.setAttribute('data-af-rail', '');
      anchor.setAttribute('data-label', 'Cierre');
      anchor.innerHTML = '<span>¿Qué te pareció?</span>';
      anchor.addEventListener('click', function(e) {
        e.preventDefault();
        var t = document.getElementById('academy-feedback');
        if (t) t.scrollIntoView({ behavior: 'smooth' });
      });
      rail.appendChild(anchor);
    }
    if (!('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        var dot = rail.querySelector('[data-af-rail]');
        if (!dot) return;
        if (e.isIntersecting) {
          rail.querySelectorAll('a').forEach(function(x) { x.classList.remove('active'); });
          dot.classList.add('active');
        } else {
          dot.classList.remove('active');
        }
      });
    }, { threshold: 0.4 });
    io.observe(fb);
  }
  function hookup(node) { observe(node); wireRail(node); }
  function replaceFeedback(html) {
    var node = document.getElementById('academy-feedback');
    if (node) { node.outerHTML = html; hookup(document.getElementById('academy-feedback')); }
  }
  document.addEventListener('submit', function(event) {
    var form = event.target.closest('[data-academy-feedback-form]');
    if (!form) return;
    event.preventDefault();
    var data = new FormData(form);
    if (event.submitter && event.submitter.name) {
      data.set(event.submitter.name, event.submitter.value || '');
    }
    fetch(form.action, { method: 'POST', body: data, credentials: 'same-origin' })
      .then(function(response) { return response.text(); })
      .then(replaceFeedback);
  });
  document.addEventListener('click', function(event) {
    var button = event.target.closest('[data-academy-feedback-cancel]');
    if (!button) return;
    event.preventDefault();
    var data = new FormData();
    data.set('pack', button.dataset.pack || '""" + pack + """');
    fetch(button.dataset.url, { method: 'POST', body: data, credentials: 'same-origin' })
      .then(function(response) { return response.text(); })
      .then(replaceFeedback);
  });
  document.addEventListener('DOMContentLoaded', function() {
    if (!document.getElementById('academy-feedback')) {
      document.body.insertAdjacentHTML('beforeend', '""" + initial + """');
    }
    hookup(document.getElementById('academy-feedback'));
  });
})();
</script>
"""


def _truthy(value: str) -> bool:
    """Checkbox HTML: manda su valor solo si está tildado."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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

    log_activity("login", user=user, job_title=str(claims.get("jobTitle") or ""))
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


@app.post("/api/diagnostic", include_in_schema=False)
def diagnostic_toggle(request: Request, on: str = Form("")):
    """Persiste el modo diagnóstico en cookie, emitida por el SERVER.

    Desde el JS del iframe enmascarado (danwor.com) una cookie SameSite=Lax no
    viaja en requests cross-site y el modo se "apagaba solo" en producción; acá
    se emite con los mismos atributos que la cookie de sesión (None+Secure en
    prod). El gate real sigue siendo _diagnostic() (cookie + APP_OWNER)."""
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    enabled = on == "on"
    response = JSONResponse({"ok": True, "diagnostic": enabled})
    response.set_cookie(
        "dw-diagnostic", "on" if enabled else "off", max_age=31536000, path="/",
        httponly=False, samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


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
            '{background:rgba(20,22,28,.30);background-clip:padding-box}'
            # Embebido a pantalla completa (embed=1): el shell del copiloto ya da
            # contexto, así que se ocultan el topbar/progreso/footer propios del HTML.
            'html[data-dw-embed] .topbar,html[data-dw-embed] .scroll-progress,html[data-dw-embed] footer{display:none!important}'
            'html[data-dw-embed] .hero{padding-top:2.5rem}</style>'
            "<script>(function(){function a(t){document.documentElement"
            ".setAttribute('data-dw-theme',t||'light');}"
            "var p=window.DANWOR_EMBED_PROPS||{};a(p.theme);"
            "if(p.embed){document.documentElement.setAttribute('data-dw-embed','1');"
            # Avisar al copiloto qué página se está viendo (para sincronizar el nav
            # izquierdo al navegar entre páginas DENTRO del iframe, p. ej. paradigmas→datasphere).
            # El propio HTML ya arrastra el query completo (branding+embed) a sus cross-links
            # (data-carry-branding), así que embed=1 persiste al navegar dentro.
            "try{parent.postMessage({type:'danwor-academy-nav',"
            "asset:(location.pathname.split('/').pop()||'')},'*');}catch(_){}}"
            "window.addEventListener('message',function(e){"
            "if(e.data&&e.data.type==='danwor-theme')a(e.data.theme);});})();</script>"
        )
        injection = f"<script>window.DANWOR_EMBED_PROPS = {payload};</script>{theme_support}"
        source = path.read_text(encoding="utf-8", errors="ignore")
        academy_feedback_stem = str(request.query_params.get("academy_feedback") or "").strip()
        academy_feedback = ""
        if academy_feedback_stem and relative.parts and relative.parts[0].lower() == "academy":
            academy_feedback = _academy_feedback_assets(academy_feedback_stem, selected)
        if academy_feedback:
            if re.search(r"</body>", source, re.I):
                source = re.sub(r"</body>", academy_feedback + "\n</body>", source, count=1, flags=re.I)
            else:
                source = source + "\n" + academy_feedback
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
    """URL de entrada a la app tras el login (perfil full): el 00 Intro.

    La intro inmersiva es la puerta narrativa del copiloto; el perfil de solo
    lectura (ACADEMY_USERS) no pasa por acá — aterriza en Danwor Academy (/home)."""
    modules = list_modules(pack)
    if not modules:
        raise HTTPException(503, "No hay módulos configurados")
    module = next(
        (item for item in modules if item.number == "00"),
        next((item for item in modules if item.number == "01"), modules[0]),
    )
    return f"/modules/{module.stem}?pack={pack}"


@app.get("/home", include_in_schema=False)
def home(request: Request, pack: str = Query(DEFAULT_PACK)):
    """Destino post-login, dependiente del perfil. Los usuarios de solo lectura
    (ACADEMY_USERS) aterrizan por defecto en Danwor Academy —su puerta de entrada—;
    los demás, en el primer módulo del marco. El contenido no se restringe: es solo
    el punto de aterrizaje inicial."""
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    if not auth_service.session_user(request):
        return RedirectResponse(f"/?pack={selected}#acceso", status_code=307)
    if _readonly(request):
        academy = [item for item in list_academy(selected) if not item.nav_hidden]
        if academy:
            return RedirectResponse(f"/academy/{academy[0].stem}?pack={selected}", status_code=307)
    return RedirectResponse(_home_url(selected), status_code=307)


# ── Invitación al Desafío (one-off) ──────────────────────────────────────
# URL directa (enviada por mail) que muestra la página del Desafío como dialog
# sobre el copiloto. No es navegable: el trigger es una cookie que sobrevive al
# login (el flujo post-login siempre aterriza en /home) y se borra al cerrar el
# dialog. Reusable: cada visita a /desafio la vuelve a armar.
INVITE_COOKIE = "dw-desafio"
INVITE_STEM = "ACAD05_Desafio"


def _invite_src(request: Request, pack: str) -> str:
    """src del iframe del dialog de invitación, o "" si no corresponde mostrarlo."""
    if request.cookies.get(INVITE_COOKIE) != "1":
        return ""
    asset = academy_asset_for(INVITE_STEM, pack)
    if not asset:
        return ""
    query = urlencode({"branding": "danwor", "embed": "1", "academy_feedback": INVITE_STEM})
    return f"/pack-assets/{pack}/academy/{asset}?{query}"


@app.get("/desafio", include_in_schema=False)
def desafio_invite(request: Request, pack: str = Query(DEFAULT_PACK)):
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    response = RedirectResponse(f"/home?pack={selected}", status_code=307)
    # Siempre rearma la invitación: si el consultor vuelve a usar el link del
    # mail, el dialog debe volver a aparecer. El "no reaparece tras cerrar" no
    # se resuelve acá sino en el cierre del dialog (base.html): enmascarado en
    # el frame de danwor.com, la cruz navega el top a la URL limpia del wrapper
    # y el refresh deja de pasar por /desafio.
    response.set_cookie(
        INVITE_COOKIE, "1", max_age=86400, path="/", httponly=False,
        samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


@app.get("/desafio/cerrar", include_in_schema=False)
def desafio_close():
    """Cierre del dialog cuando la app corre enmascarada en el frame de danwor.com.

    La cruz navega el TOP a este endpoint: al ser una navegación de primer nivel
    al host de la app, la cookie de invitación se borra en contexto first-party
    (desde el JS del iframe es third-party y el borrado no es confiable) y recién
    después se redirige a la URL limpia del wrapper."""
    response = RedirectResponse(DESAFIO_CLOSE_URL, status_code=307)
    response.delete_cookie(
        INVITE_COOKIE, path="/",
        samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
    )
    return response


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(request: Request, pack: str = Query(DEFAULT_PACK)):
    selected = _pack(pack)
    # Con sesión vigente, la raíz sigue de largo al copiloto: el wrapper de
    # danwor.com (/copiloto/) embebe esta raíz, y un usuario ya logueado debe
    # aterrizar adentro de la app, no en el landing con la sección de acceso.
    if auth_service.session_user(request):
        return RedirectResponse(f"/home?pack={selected}", status_code=307)
    # data-home apunta a /home (redirección server-side según perfil), no directo al
    # módulo 01: así el perfil solo-lectura aterriza en Danwor Academy tras el login.
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"pack": selected, "home_url": f"/home?pack={selected}"},
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
    all_appendices = list_appendices(selected)
    # Apéndices con nav_hidden (p. ej. A06 en revisión) salen del nav y del pager,
    # pero la página sigue resoluble por URL directa (para trabajarla). is_appendix
    # se calcula sobre la lista completa para que su render (eyebrow "Apéndice")
    # sea correcto si se entra directo.
    appendices = [a for a in all_appendices if not a.nav_hidden]
    academy = [item for item in list_academy(selected) if not item.nav_hidden]
    try:
        document = load_module(stem, selected)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Módulo no encontrado") from exc
    log_activity("module_selected", user=user, module=stem, pack=selected)

    # Nav combinado: módulos principales + apéndices técnicos (módulo final).
    # Danwor Academy queda FUERA de nav_items a propósito: es un área desconectada
    # de la navegación (no entra en el pager prev/next ni en el bucle de scroll).
    nav_items = (*modules, *appendices)
    is_appendix = any(item.stem == stem for item in all_appendices)
    ia_enabled = ia_enabled_for(stem)

    # Layout inmersivo (p. ej. 00 Intro): en vez de renderizar secciones markdown,
    # se embebe un HTML de storytelling (formato Academy) a pantalla completa. El
    # módulo sigue en el nav Core con su pager/loop; solo cambia el cuerpo.
    _meta, _ = split_front_matter(document.source)
    immersive = str(_meta.get("layout") or "").strip().lower() == "immersive"
    embed_asset = str(_meta.get("embed_asset") or "").strip()
    immersive = immersive and bool(embed_asset) and "/" not in embed_asset and ".." not in embed_asset
    current_index = next((i for i, item in enumerate(nav_items) if item.stem == stem), None)
    previous_module = nav_items[current_index - 1] if current_index else None
    next_module = (
        nav_items[current_index + 1]
        if current_index is not None and current_index + 1 < len(nav_items)
        else None
    )
    # Bucle de lectura: en el último documento, la continuación por scroll vuelve
    # al inicio (Intro) en vez de "morir". Solo aplica al cue de scroll continuo,
    # no a las flechas del pager (que siguen deshabilitándose al final).
    loop_home = nav_items[0] if (next_module is None and len(nav_items) > 1) else None

    # En modo inmersivo el scroll ocurre dentro del iframe, así que el cue de
    # "scroll para continuar" vive en el HTML embebido: le pasamos a dónde ir
    # (siguiente módulo, o loop al inicio) para que navegue el top al llegar al final.
    iframe_src = None
    if immersive:
        _cont = next_module or loop_home
        _params = {"branding": "danwor", "embed": "1"}
        if _cont:
            _prefix = _cont.stem.split("_")[0]
            _params["continue_url"] = f"/modules/{_cont.stem}?pack={selected}"
            _params["continue_label"] = (
                f"{_prefix} · {_cont.title}" if next_module else f"↻ {_prefix} · {_cont.title}"
            )
        iframe_src = f"/pack-assets/{selected}/{embed_asset}?{urlencode(_params)}"
    trees = store.trees_for_module(stem, selected, include_assessment=True)
    run = store.start(trees[0], selected) if len(trees) == 1 else None
    conversation = assistant_store.start(stem, selected)
    # Perfil de solo lectura (ACADEMY_USERS): lee todo, pero sin IA. Conserva el
    # panel SOLO si el módulo tiene árboles de decisión (deterministas, sin tokens);
    # las pestañas Sección/Asistente (IA) se ocultan en el template.
    readonly = _readonly(request)
    has_decision_tree = any(t.get("kind") != "assessment" for t in trees)
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
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "appendix" if is_appendix else "core",
            "active_stem": stem,
            "ia_enabled": ia_enabled,
            "is_appendix": is_appendix,
            "diagnostic": diagnostic,
            "is_owner": _is_owner(request),
            "immersive": immersive,
            "iframe_src": iframe_src,
            "readonly": readonly,
            # El panel derecho se muestra si el módulo tiene IA (grupo Decisión) o
            # si está el modo diagnóstico activo (grupo Gestión). Nunca en módulos
            # inmersivos (p. ej. 00 Intro). Para el perfil de solo lectura se muestra
            # si hay asistente (router determinista) o árbol; la salida LLM queda gateada.
            "show_panel": (
                ((ia_enabled or has_decision_tree) and not immersive) if readonly
                else ((ia_enabled or diagnostic) and not immersive)
            ),
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
            "loop_home": loop_home,
            "section_actions": ALL_ACTIONS,
            "module_actions": MODULE_ACTIONS,
            # Descripciones y mensajes de carga combinados (sección + módulo) para el
            # template, que alterna las acciones según el target activo.
            "all_action_descriptions": {**ACTION_DESCRIPTIONS, **MODULE_ACTION_DESCRIPTIONS},
            "all_loading_messages": {
                action: get_loading_message(action, False)
                for action in (*ALL_ACTIONS, *MODULE_ACTIONS)
            },
            "user": user,
            "conversation_id": conversation.id,
            "invite_src": _invite_src(request, selected),
        },
    )


@app.get("/academy/{stem}", response_class=HTMLResponse, include_in_schema=False)
def academy_page(request: Request, stem: str, pack: str = Query(DEFAULT_PACK)):
    """Danwor Academy: área de onboarding desconectada de la navegación.

    Embebe un HTML de storytelling autónomo (assets/academy/*.html) a pantalla
    completa, sin panel de IA (como el módulo 00). El theme del copiloto viaja al
    iframe por la infra de /pack-assets (data-dw-theme + postMessage)."""
    diagnostic = _diagnostic(request)
    selected = _pack(pack) if diagnostic else DEFAULT_PACK
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={selected}#acceso", status_code=307)

    asset = academy_asset_for(stem, selected)
    if not asset:
        raise HTTPException(404, "Página de Academy no encontrada")
    current = next((item for item in list_academy(selected) if item.stem == stem), None)
    academy_nav = [item for item in list_academy(selected) if not item.nav_hidden]
    log_activity("academy_selected", user=user, page=stem, pack=selected)

    # branding=danwor → colores/marca Danwor; embed=1 → oculta el topbar propio del
    # HTML (el shell del copiloto ya da contexto). El theme lo inyecta pack_asset.
    iframe_src = (
        f"/pack-assets/{selected}/academy/{asset}?"
        f"{urlencode({'branding': 'danwor', 'embed': '1', 'academy_feedback': stem})}"
    )
    packs = [
        {"name": name, "label": content_pack_short_label(name), "description": content_pack_label(name), "home": _home_url(name)}
        for name in available_content_packs()
    ]
    return templates.TemplateResponse(
        request=request,
        name="academy.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy_nav,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy_nav},
            "active_group": "academy",
            "active_stem": stem,
            "readonly": _readonly(request),
            "page_title": current.title if current else "Danwor Academy",
            "iframe_src": iframe_src,
            "show_panel": False,
            "diagnostic": diagnostic,
            "is_owner": _is_owner(request),
            "pack": selected,
            "packs": packs,
            "pack_label": content_pack_short_label(selected),
            "user": user,
            "invite_src": _invite_src(request, selected),
        },
    )


def _save_academy_feedback(request: Request, stem: str, pack: str, rating: str, comment: str = "") -> None:
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    selected = _pack(pack)
    current = next((item for item in list_academy(selected) if item.stem == stem), None)
    if current is None:
        raise HTTPException(404, "Página de Academy no encontrada")
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "academy_content",
            "academy_page": current.stem,
            "academy_title": current.title,
            "pack": selected,
            "rating": rating,
            "comment": comment.strip(),
            "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
            "ts": now.isoformat(),
        }
        path = FEEDBACK_LOG_DIR / f"academy_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        # El feedback no debe romper la navegación ni el contenido de Academy.
        pass


@app.post("/academy/{stem}/feedback", response_class=HTMLResponse, include_in_schema=False)
def academy_feedback(request: Request, stem: str, rating: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    if rating == "down":
        return HTMLResponse(_academy_feedback_html(stem, selected, form=True))
    _save_academy_feedback(request, stem, selected, "up")
    return HTMLResponse(_academy_feedback_html(stem, selected, submitted=True, rating="up"))


@app.post("/academy/{stem}/feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def academy_feedback_submit(
    request: Request,
    stem: str,
    rating: str = Form("down"),
    comment: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = _pack(pack)
    if rating == "down" and not comment.strip():
        return HTMLResponse(_academy_feedback_html(stem, selected, form=True, error="Contanos brevemente qué faltó."))
    _save_academy_feedback(request, stem, selected, rating, comment)
    return HTMLResponse(_academy_feedback_html(stem, selected, submitted=True, rating=rating))


@app.post("/academy/{stem}/feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def academy_feedback_cancel(stem: str, pack: str = Form(DEFAULT_PACK)):
    return HTMLResponse(_academy_feedback_html(stem, _pack(pack)))


@app.post("/modules/{stem}/sections/analyze", response_class=HTMLResponse)
def analyze_section(
    request: Request,
    stem: str,
    section_slug: str = Form(""),
    action: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
    conversation_id: str = Form(""),
):
    if _readonly(request):
        return HTMLResponse(
            '<p class="section-analysis-warning-copy">IA no disponible para Danwor Academy: '
            'el análisis de sección genera contenido con IA y está deshabilitado en tu perfil por ahora.</p>',
            status_code=403,
        )
    selected = _pack(pack)
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
            "llm_answer": reply is not None,
        },
    )



def _save_module_feedback(request: Request, document, selected: str, rating: str, comment: str = "") -> None:
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "module_content",
            "module": document.summary.stem,
            "module_title": document.summary.title,
            "pack": selected,
            "rating": rating,
            "user": str(user.get("username") or user.get("email") or ""),
            "ts": now.isoformat(),
        }
        clean_comment = (comment or "").strip()
        if clean_comment:
            payload["comment"] = clean_comment
        path = FEEDBACK_LOG_DIR / f"module_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        # El feedback no debe romper la navegacion ni el contenido del modulo.
        pass


@app.post("/modules/{stem}/feedback", response_class=HTMLResponse)
def module_feedback(
    request: Request,
    stem: str,
    rating: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = _pack(pack)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating invalido")
    document = load_module(stem, selected)
    if rating == "down":
        return templates.TemplateResponse(
            request=request,
            name="partials/module_feedback.html",
            context={"document": document, "pack": selected, "form": True, "error": ""},
        )
    _save_module_feedback(request, document, selected, "up")
    return templates.TemplateResponse(
        request=request,
        name="partials/module_feedback.html",
        context={"document": document, "pack": selected, "submitted": True, "rating": "up"},
    )


@app.post("/modules/{stem}/feedback/submit", response_class=HTMLResponse)
def module_feedback_submit(
    request: Request,
    stem: str,
    rating: str = Form("down"),
    comment: str = Form(""),
    pack: str = Form(DEFAULT_PACK),
):
    selected = _pack(pack)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating invalido")
    document = load_module(stem, selected)
    if rating == "down" and not comment.strip():
        return templates.TemplateResponse(
            request=request,
            name="partials/module_feedback.html",
            context={
                "document": document,
                "pack": selected,
                "form": True,
                "error": "Contanos brevemente que falto o que mejorarias.",
            },
        )
    _save_module_feedback(request, document, selected, rating, comment)
    return templates.TemplateResponse(
        request=request,
        name="partials/module_feedback.html",
        context={"document": document, "pack": selected, "submitted": True, "rating": rating},
    )


@app.post("/modules/{stem}/feedback/cancel", response_class=HTMLResponse)
def module_feedback_cancel(request: Request, stem: str, pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    document = load_module(stem, selected)
    return templates.TemplateResponse(
        request=request,
        name="partials/module_feedback.html",
        context={"document": document, "pack": selected},
    )


# ── Feedback por sección (micro-botonera al cierre de cada sección) ────────
# Granularidad fina: complementa el feedback general de módulo. Es contenido
# (sin IA/tokens), así que lo ve también el perfil de solo lectura. El comentario
# del 👎 es OPCIONAL. Escribe type=section_content (lo analiza analyze_feedback).

def _section_or_404(document, section_slug: str):
    section = next((s for s in document.sections if s.slug == section_slug), None)
    if section is None:
        raise HTTPException(404, "Sección no encontrada")
    return section


def _section_feedback_ctx(document, section, selected: str, **extra) -> dict:
    return {"stem": document.summary.stem, "section": section, "pack": selected, **extra}


def _save_section_feedback(request: Request, document, section, selected: str, rating: str, comment: str = "") -> None:
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "section_content",
            "module": document.summary.stem,
            "module_title": document.summary.title,
            "section_slug": section.slug,
            "section_title": section.title,
            "pack": selected,
            "rating": rating,
            "user": str(user.get("username") or user.get("email") or ""),
            "ts": now.isoformat(),
        }
        clean_comment = (comment or "").strip()
        if clean_comment:
            payload["comment"] = clean_comment
        path = FEEDBACK_LOG_DIR / f"section_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        # El feedback no debe romper la navegación ni el contenido del módulo.
        pass


@app.post("/modules/{stem}/sections/{section_slug}/feedback", response_class=HTMLResponse)
def section_feedback(request: Request, stem: str, section_slug: str,
                     rating: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    document = load_module(stem, selected)
    section = _section_or_404(document, section_slug)
    if rating == "down":
        # El 👎 despliega la línea de comentario opcional; todavía no persiste.
        return templates.TemplateResponse(
            request=request, name="partials/section_feedback.html",
            context=_section_feedback_ctx(document, section, selected, form=True),
        )
    _save_section_feedback(request, document, section, selected, "up")
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_section_feedback_ctx(document, section, selected, submitted=True, rating="up"),
    )


@app.post("/modules/{stem}/sections/{section_slug}/feedback/submit", response_class=HTMLResponse)
def section_feedback_submit(request: Request, stem: str, section_slug: str,
                            rating: str = Form("down"), comment: str = Form(""),
                            pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    document = load_module(stem, selected)
    section = _section_or_404(document, section_slug)
    # Comentario opcional: el 👎 se registra siempre (con o sin texto).
    _save_section_feedback(request, document, section, selected, "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_section_feedback_ctx(document, section, selected, submitted=True, rating="down"),
    )


@app.post("/modules/{stem}/sections/{section_slug}/feedback/cancel", response_class=HTMLResponse)
def section_feedback_cancel(request: Request, stem: str, section_slug: str, pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    document = load_module(stem, selected)
    section = _section_or_404(document, section_slug)
    return templates.TemplateResponse(
        request=request, name="partials/section_feedback.html",
        context=_section_feedback_ctx(document, section, selected),
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
    # Perfil solo-lectura: el asistente SÍ funciona (el router interno es determinista
    # y no consume OpenAI), pero cualquier salida que dispararía la API de OpenAI
    # (chat libre sin ruta, "Explicame vos") se reemplaza por un aviso, sin gastar tokens.
    readonly = _readonly(request)
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
            )
        reply = assistant_store.append(
            conversation_id, stem, selected, question.strip(), answer, answer_html,
            topic=route_heading or "Pregunta libre",
            action="Routing" if route else ("IA bloqueada" if ia_blocked else "Asistente"),
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
            "llm_answer": route is None and reply is not None and not readonly,
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


def _resolve_tree(trees: tuple, tree_id: str, tree_title: str, pack_name: str) -> dict | None:
    """Resuelve un árbol por id exacto; si no, por título (exacto, luego por
    subconjunto de tokens — p. ej. el botón 'Estrategia de Integración (federación
    vs persistencia)' matchea el árbol 'Federación vs Persistencia')."""
    if tree_id:
        tree = next((t for t in trees if str(t.get("id")) == tree_id), None)
        if tree:
            return tree
        tree = store.tree_by_id(tree_id, pack_name)
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
    panel: str = Form("tree"),
):
    selected = _pack(pack)
    trees = store.trees_for_module(stem, selected, include_assessment=True)
    tree = _resolve_tree(trees, tree_id, tree_title, selected)
    if tree is None:
        raise HTTPException(404, "Árbol no encontrado")
    run = store.start(tree, selected)
    partial_name = "partials/diagnostic_tab.html" if panel == "diagnostic" else "partials/tree_tab.html"
    return templates.TemplateResponse(
        request=request,
        name=partial_name,
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



@app.post("/modules/{stem}/diagnostics/orchestrator", response_class=HTMLResponse)
def start_diagnostic_orchestrator(
    request: Request,
    stem: str,
    pack: str = Form(DEFAULT_PACK),
):
    selected = _pack(pack)
    trees = store.trees_for_module(stem, selected, include_routing_only=True, include_assessment=True)
    tree = next((item for item in trees if str(item.get("id")) == "framework_assessment"), None)
    if tree is None:
        raise HTTPException(404, "Diagn\u00f3stico transversal no encontrado")
    run = store.start(tree, selected)
    return templates.TemplateResponse(
        request=request,
        name="partials/wizard.html",
        context={"run": run, "node": run.node, "leaf": run.leaf, "pack": selected},
    )

def _follow_wizard_redirect(run):
    leaf = run.leaf or {}
    post_actions = leaf.get("post_actions") or {}
    target_tree_id = str(leaf.get("redirect_tree") or "").strip()
    if post_actions.get("mode") == "assessment_orchestration":
        sequence = [str(item).strip() for item in (leaf.get("next_tree_sequence") or []) if str(item).strip()]
        target_tree_id = sequence[0] if sequence else ""
        target_tree = store.tree_by_id(target_tree_id, run.pack_name) if target_tree_id else None
        if target_tree is None:
            return run
        return store.start(
            target_tree,
            run.pack_name,
            orchestration_sequence=sequence,
            orchestration_index=0,
            orchestration_scores=[],
        )
    if target_tree_id:
        target_tree = store.tree_by_id(target_tree_id, run.pack_name)
        if target_tree is None:
            return run
        return store.start(target_tree, run.pack_name)
    if run.is_orchestrated and run.leaf is not None and run.orchestration_index < len(run.orchestration_sequence) - 1:
        scores = list(run.orchestration_scores)
        current_score = run.assessment_score
        if current_score:
            item = dict(current_score)
            item["tree_id"] = str(run.tree.get("id") or "")
            item["title"] = run.display_title
            scores.append(item)
        next_index = run.orchestration_index + 1
        next_tree_id = run.orchestration_sequence[next_index]
        next_tree = store.tree_by_id(next_tree_id, run.pack_name)
        if next_tree is None:
            return run
        return store.start(
            next_tree,
            run.pack_name,
            orchestration_sequence=run.orchestration_sequence,
            orchestration_index=next_index,
            orchestration_scores=scores,
        )
    return run


def _fragment(request, run):
    return templates.TemplateResponse(
        request=request,
        name="partials/wizard.html",
        context={"run": run, "node": run.node, "leaf": run.leaf, "pack": run.pack_name},
    )


@app.post("/wizards/{run_id}/answer", response_class=HTMLResponse)
def answer(request: Request, run_id: str, answer: str = Form("")):
    try:
        run = store.answer(run_id, answer)
        return _fragment(request, _follow_wizard_redirect(run))
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


# =========================================================
# Herramientas de gestión (Analítica interna) — solo diagnóstico
# =========================================================

def _require_diagnostic(request: Request) -> None:
    if not _diagnostic(request):
        raise HTTPException(403, "Solo disponible en modo diagnóstico")


def _admin_result(request: Request, title: str, output: str):
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_result.html",
        context={"title": title, "output": output},
    )


@app.get("/admin/runtime/inventory", response_class=HTMLResponse)
def admin_runtime_inventory(request: Request):
    _require_diagnostic(request)
    rows, total_files, total_size, _latest = admin_tools.runtime_inventory()
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_runtime.html",
        context={
            "rows": rows,
            "total_files": total_files,
            "total_size": admin_tools._format_bytes(total_size),
        },
    )


@app.get("/admin/runtime/export.zip")
def admin_runtime_export(request: Request):
    _require_diagnostic(request)
    payload = admin_tools.runtime_export_zip()
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{admin_tools.runtime_export_filename()}"'
        },
    )


@app.post("/admin/runtime/purge", response_class=HTMLResponse)
def admin_runtime_purge(
    request: Request,
    folders: list[str] = Form([]),
    date_from: str = Form(""),
    date_to: str = Form(""),
    mode: str = Form("preview"),
):
    _require_diagnostic(request)
    output = admin_tools.runtime_purge(folders, date_from, date_to, dry_run=(mode != "delete"))
    return _admin_result(request, "Borrado selectivo de runtime", output)


@app.post("/admin/cases/analyze", response_class=HTMLResponse)
def admin_cases_analyze(request: Request, llm: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_cases_analysis(use_llm=_truthy(llm))
    return _admin_result(request, "Case Library — análisis", output)


@app.post("/admin/feedback/analyze", response_class=HTMLResponse)
def admin_feedback_analyze(request: Request, kind: str = Form("all")):
    _require_diagnostic(request)
    output = admin_tools.run_feedback_analysis(kind=kind)
    return _admin_result(request, "Feedback — análisis", output)


@app.post("/admin/trees/propose", response_class=HTMLResponse)
def admin_trees_propose(request: Request, apply: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_tree_proposals(apply=_truthy(apply))
    return _admin_result(request, "Propuestas de árbol", output)


@app.post("/admin/content/propose", response_class=HTMLResponse)
def admin_content_propose(request: Request, llm: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_content_proposals(use_llm=_truthy(llm))
    return _admin_result(request, "Propuestas de contenido", output)


def _owner_key(request: Request) -> str:
    user = auth_service.session_user(request) or {}
    return str(user.get("sub") or "anon").strip().lower()


@app.post("/admin/sharepoint/refresh-cache", response_class=HTMLResponse)
def admin_sharepoint_refresh(request: Request, graph_token: str = Form("")):
    _require_diagnostic(request)
    message = admin_tools.sharepoint_refresh_cache(graph_token)
    return _admin_result(request, "SharePoint — cache", message)


@app.post("/admin/sharepoint/seed", response_class=HTMLResponse)
def admin_sharepoint_seed(request: Request, graph_token: str = Form("")):
    _require_diagnostic(request)
    preview, count = admin_tools.run_sharepoint_seed(graph_token, _owner_key(request))
    return templates.TemplateResponse(
        request=request,
        name="partials/admin_sharepoint.html",
        context={"preview": preview, "count": count},
    )


@app.get("/admin/sharepoint/seed.json")
def admin_sharepoint_seed_download(request: Request):
    _require_diagnostic(request)
    payload = admin_tools.last_seed(_owner_key(request))
    if not payload:
        raise HTTPException(404, "No hay seed generado en esta sesión")
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sharepoint_cases_semantic_seed.json"'},
    )


if __name__ == "__main__":
    # `python -m webapp.main` levanta en 8501, el puerto cuyo redirect URI
    # (http://localhost:8501/) ya está registrado en Azure para el login MSAL.
    import uvicorn

    uvicorn.run("webapp.main:app", host="127.0.0.1", port=8501, reload=True)
