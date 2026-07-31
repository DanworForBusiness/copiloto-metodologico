from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from uuid import uuid4
import html
import json
import os
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
from webapp.services import whats_new_service as whatsnew
from webapp.services import guides_service as guides

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


_CATALOG_VERDICT_TEXT = {
    "plena": "Referencia Danwor — reutilizable",
    "buena": "Reutilizable con notas",
    "parcial": "Inspiración conceptual con reservas — no copiar tal cual",
}


def _catalog_verdict_key(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if raw in {"plena", "green", "verde"}:
        return "plena"
    if raw in {"buena", "yellow", "amarillo"}:
        return "buena"
    if raw in {"parcial", "orange", "naranja"}:
        return "parcial"
    return raw or "sin-dictamen"


def _catalog_verdict_label(value: str) -> str:
    return _catalog_verdict_key(value).replace("-", " ").upper()


def _catalog_verdict_text(value: str) -> str:
    return _CATALOG_VERDICT_TEXT.get(_catalog_verdict_key(value), "Referencia Danwor")


templates.env.filters["catalog_verdict_class"] = _catalog_verdict_key
templates.env.filters["catalog_verdict_label"] = _catalog_verdict_label
templates.env.filters["catalog_verdict_text"] = _catalog_verdict_text

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


_MODULE_TABS = {"section", "tree", "catalogo", "assistant"}


@app.get("/modules/{stem}", response_class=HTMLResponse)
def module_page(request: Request, stem: str, pack: str = Query(DEFAULT_PACK), tab: str = ""):
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

    # Pestaña inicial del panel derecho vía URL (?tab=…). Permite que un link (p. ej. de
    # Novedades) abra el módulo YA posicionado en Catálogo/Árbol/etc. Se valida: si la
    # pestaña no aplica a este módulo, se ignora (el default de copilotLayout gana).
    initial_tab = (tab or "").strip().lower()
    if initial_tab not in _MODULE_TABS:
        initial_tab = ""
    elif initial_tab == "catalogo":
        _num = str(document.summary.number)
        if not (_catalog_domains() and _num != "05" and not document.summary.stem.startswith("05_")):
            initial_tab = ""
    elif initial_tab == "section" and _readonly(request):
        initial_tab = ""
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
            "catalog_pilot": os.getenv("DANWOR_CATALOG_PILOT", "").lower() in {"1", "true", "on", "yes"},
            "catalog_domains": _catalog_domains(),
            "catalog_connection_types": _catalog_connection_types(),
            "catalog_security_families": _catalog_security_families(),
            "catalog_data_products": _catalog_data_products_list(),
            "initial_tab": initial_tab,
            "whatsnew_unseen": whatsnew.is_unseen(request, selected),
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


# ── Feedback de cierre de catálogo / Novedades (partial catalog_feedback.html) ──
def _save_catalog_feedback(request: Request, kind: str, ref: str, pack: str, rating: str, comment: str = "") -> None:
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "catalog", "catalog_kind": kind or "", "catalog_ref": ref or "",
            "pack": pack, "rating": rating,
            "user": str(user.get("username") or user.get("email") or ""), "ts": now.isoformat(),
        }
        c = (comment or "").strip()
        if c:
            payload["comment"] = c
        path = FEEDBACK_LOG_DIR / f"catalog_feedback_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass


def _catalog_feedback_ctx(kind: str, ref: str, pack: str, fb_eyebrow: str = "", fb_prompt: str = "", **extra) -> dict:
    return {"catalog_kind": kind, "catalog_ref": ref, "pack": pack,
            "fb_eyebrow": fb_eyebrow, "fb_prompt": fb_prompt, **extra}


@app.post("/catalog/feedback", response_class=HTMLResponse, include_in_schema=False)
def catalog_feedback(request: Request, rating: str = Form(""), catalog_kind: str = Form(""),
                     catalog_ref: str = Form(""), fb_eyebrow: str = Form(""), fb_prompt: str = Form(""),
                     pack: str = Form(DEFAULT_PACK)):
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    selected = _pack(pack)
    if rating == "down":   # el 👎 despliega el comentario opcional; todavía no persiste
        return templates.TemplateResponse(
            request=request, name="partials/catalog_feedback.html",
            context=_catalog_feedback_ctx(catalog_kind, catalog_ref, selected, fb_eyebrow, fb_prompt, form=True))
    _save_catalog_feedback(request, catalog_kind, catalog_ref, selected, "up")
    return templates.TemplateResponse(
        request=request, name="partials/catalog_feedback.html",
        context=_catalog_feedback_ctx(catalog_kind, catalog_ref, selected, fb_eyebrow, fb_prompt, submitted=True, rating="up"))


@app.post("/catalog/feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def catalog_feedback_submit(request: Request, comment: str = Form(""), catalog_kind: str = Form(""),
                            catalog_ref: str = Form(""), fb_eyebrow: str = Form(""), fb_prompt: str = Form(""),
                            pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    _save_catalog_feedback(request, catalog_kind, catalog_ref, selected, "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/catalog_feedback.html",
        context=_catalog_feedback_ctx(catalog_kind, catalog_ref, selected, fb_eyebrow, fb_prompt, submitted=True, rating="down"))


@app.post("/catalog/feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def catalog_feedback_cancel(request: Request, catalog_kind: str = Form(""), catalog_ref: str = Form(""),
                            fb_eyebrow: str = Form(""), fb_prompt: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    return templates.TemplateResponse(
        request=request, name="partials/catalog_feedback.html",
        context=_catalog_feedback_ctx(catalog_kind, catalog_ref, _pack(pack), fb_eyebrow, fb_prompt))


# ── Micro-feedback por sección del detalle de catálogo (simil módulos) ──────
def _save_catalog_section_feedback(request: Request, cliente: str, dominio: str, slug: str,
                                   pack: str, rating: str, comment: str = "") -> None:
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "catalog_section", "cliente": cliente, "dominio": dominio, "section_slug": slug,
            "pack": pack, "rating": rating,
            "user": str(user.get("username") or user.get("email") or ""), "ts": now.isoformat(),
        }
        c = (comment or "").strip()
        if c:
            payload["comment"] = c
        path = FEEDBACK_LOG_DIR / f"catalog_section_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        pass


def _catalog_section_fb_ctx(cliente: str, dominio: str, slug: str, pack: str, fb_prompt: str = "", **extra) -> dict:
    return {"cliente": cliente, "dominio": dominio, "slug": slug, "pack": pack, "fb_prompt": fb_prompt, **extra}


@app.post("/catalog/section-feedback", response_class=HTMLResponse, include_in_schema=False)
def catalog_section_feedback(request: Request, cliente: str = Form(""), dominio: str = Form(""),
                             slug: str = Form(""), rating: str = Form(""), fb_prompt: str = Form(""),
                             pack: str = Form(DEFAULT_PACK)):
    if rating not in {"up", "down"}:
        raise HTTPException(400, "Rating inválido")
    selected = _pack(pack)
    if rating == "down":
        return templates.TemplateResponse(
            request=request, name="partials/catalog_section_feedback.html",
            context=_catalog_section_fb_ctx(cliente, dominio, slug, selected, fb_prompt, form=True))
    _save_catalog_section_feedback(request, cliente, dominio, slug, selected, "up")
    return templates.TemplateResponse(
        request=request, name="partials/catalog_section_feedback.html",
        context=_catalog_section_fb_ctx(cliente, dominio, slug, selected, fb_prompt, submitted=True, rating="up"))


@app.post("/catalog/section-feedback/submit", response_class=HTMLResponse, include_in_schema=False)
def catalog_section_feedback_submit(request: Request, cliente: str = Form(""), dominio: str = Form(""),
                                    slug: str = Form(""), comment: str = Form(""), fb_prompt: str = Form(""),
                                    pack: str = Form(DEFAULT_PACK)):
    selected = _pack(pack)
    _save_catalog_section_feedback(request, cliente, dominio, slug, selected, "down", comment)
    return templates.TemplateResponse(
        request=request, name="partials/catalog_section_feedback.html",
        context=_catalog_section_fb_ctx(cliente, dominio, slug, selected, fb_prompt, submitted=True, rating="down"))


@app.post("/catalog/section-feedback/cancel", response_class=HTMLResponse, include_in_schema=False)
def catalog_section_feedback_cancel(request: Request, cliente: str = Form(""), dominio: str = Form(""),
                                    slug: str = Form(""), fb_prompt: str = Form(""), pack: str = Form(DEFAULT_PACK)):
    return templates.TemplateResponse(
        request=request, name="partials/catalog_section_feedback.html",
        context=_catalog_section_fb_ctx(cliente, dominio, slug, _pack(pack), fb_prompt))


# ── "¿Falta un árbol?" — sugerencia de nuevo árbol de decisión (recuperada) ──
def _save_tree_suggestion(request: Request, module: str, title: str, detail: str, pack: str, kind: str = "tree") -> None:
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "tree_suggestion", "kind": kind, "module": module, "title": title, "pack": pack,
            "user": str(user.get("username") or user.get("email") or ""), "ts": now.isoformat(),
        }
        d = (detail or "").strip()
        if d:
            payload["detail"] = d
        path = FEEDBACK_LOG_DIR / f"tree_suggestion_{now.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        pass


@app.post("/trees/suggest", response_class=HTMLResponse, include_in_schema=False)
def trees_suggest(request: Request, title: str = Form(""), detail: str = Form(""),
                  module: str = Form(""), pack: str = Form(DEFAULT_PACK),
                  kind: str = Form("tree"), sg_id: str = Form("tree-suggest")):
    selected = _pack(pack)
    clean = (title or "").strip()
    if kind not in {"tree", "option", "section"}:
        kind = "tree"
    if clean:
        _save_tree_suggestion(request, module, clean, detail, selected, kind)
    return templates.TemplateResponse(
        request=request, name="partials/tree_suggest.html",
        context={"suggested": bool(clean), "pack": selected, "stem": module,
                 "sg_kind": kind, "sg_id": sg_id})


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
    explain_prompt: str = Form(""),
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
                directive=explain_prompt,
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


@app.post("/admin/reconciliation/analyze", response_class=HTMLResponse)
def admin_reconciliation_analyze(request: Request):
    _require_diagnostic(request)
    output = admin_tools.run_reconciliation_analysis()
    return _admin_result(request, "Reconciliación diagnóstico ⇄ automatización", output)


def _catalog_entries():
    """Carga el catálogo local (catalog_local) vía el servicio del piloto.
    Determinístico, sin IA. Devuelve (módulo, entradas) o (None, [])."""
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_catalog(cat_dir)
    except Exception:
        return None, []


def _catalog_domains() -> list:
    mod, entries = _catalog_entries()
    return mod.domains(entries) if mod else []


def _catalog_architecture_connections():
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_architecture_connections(cat_dir)
    except Exception:
        return None, []


def _catalog_connection_types() -> list:
    mod, entries = _catalog_architecture_connections()
    return mod.connection_types(entries) if mod else []




def _catalog_data_products_entries():
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_data_products(cat_dir)
    except Exception:
        return None, []


def _catalog_data_products_list() -> list:
    _mod, entries = _catalog_data_products_entries()
    return entries


def _catalog_data_product_artifact_name(name: str) -> str:
    tail = str(name or "").strip()
    tail = tail.split(":", 1)[1] if ":" in tail else tail
    tail = tail.split(".", 1)[1] if "." in tail else tail
    return tail.strip() or str(name or "").strip()


def _catalog_data_product_entity_role(display: str, index: int) -> str:
    raw = str(display or "").upper()
    if raw.startswith(("3TF", "3VF", "3VS")):
        return "Entidad principal"
    if raw.startswith(("2TD", "2VD", "2V")):
        return "Entidad relacionada"
    return "Entidad publicada" if index == 0 else "Entidad complementaria"


def _catalog_data_product_section_slug(display: str, index: int) -> str:
    role = _catalog_data_product_entity_role(display, index)
    return slugify(f"{role} · {display}")


def _catalog_data_product_lineage_svg(product, show_lineage: bool = False) -> str:
    entities = []
    seen = set()
    for ent in getattr(product, "entities", []) or []:
        if not isinstance(ent, dict):
            continue
        if str(ent.get("kind") or "").strip().lower() != "entity":
            continue
        raw_name = str(ent.get("name") or "")
        display = _catalog_data_product_artifact_name(raw_name)
        key = display.lower()
        if not display or key in seen:
            continue
        seen.add(key)
        entities.append({"name": raw_name, "display": display})
    if not entities:
        return ""

    if show_lineage and getattr(product, "lineage_nodes", None):
        published_by_display = {ent["display"].lower(): idx for idx, ent in enumerate(entities)}
        published_node_names: list[str] = []
        lineage_nodes = []
        extra_index = len(entities)
        used_names: set[str] = set()

        def _layer(node: dict, is_product: bool = False) -> int:
            if is_product:
                return 5
            if node.get("runtime") == "object_store":
                return 4
            if node.get("remote_entity"):
                return 1
            raw = (str(node.get("display") or node.get("technical_name") or node.get("name") or "")).upper()
            if raw.startswith(("0", "1", "SAPI", "ABAP")):
                return 1
            if raw.startswith("2"):
                return 2
            if raw.startswith("3"):
                return 3
            return 3

        for raw_node in getattr(product, "lineage_nodes", []) or []:
            if not isinstance(raw_node, dict):
                continue
            name = str(raw_node.get("name") or "").strip()
            display = str(raw_node.get("display") or _catalog_data_product_artifact_name(name)).strip()
            if not name or name in used_names:
                continue
            used_names.add(name)
            disp_key = display.lower()
            runtime = str(raw_node.get("runtime") or "")
            is_pub = bool(raw_node.get("published")) or disp_key in published_by_display
            if is_pub:
                idx = published_by_display.get(disp_key, len(published_node_names))
                num = idx + 2
                slug = _catalog_data_product_section_slug(display, idx)
                if runtime == "object_store":
                    published_node_names.append(name)
            else:
                extra_index += 1
                num = extra_index + 1
                slug = str(raw_node.get("slug") or slugify(f"{raw_node.get('kind_label') or raw_node.get('role') or 'Artefacto'} · {display}"))
            title_parts = [str(raw_node.get("label") or raw_node.get("kind_label") or display)]
            if runtime:
                title_parts.append("Object Store" if runtime == "object_store" else "HANA")
            if raw_node.get("delta_capable"):
                title_parts.append("delta capable")
            lineage_nodes.append({
                "name": name,
                "label": display,
                "title": " · ".join([p for p in title_parts if p]),
                "layer": _layer(raw_node),
                "upstreams": [str(x) for x in raw_node.get("upstreams") or []],
                "assoc_upstreams": [str(x) for x in raw_node.get("assoc_upstreams") or []],
                "slug": slug,
                "num": num,
                "is_factcube": False,
                "runtime": runtime,
                "subtext": "delta capable" if raw_node.get("delta_capable") else "",
            })
        if lineage_nodes:
            product_label = getattr(product, "business_name", "") or getattr(product, "title", "") or getattr(product, "product_id", "")
            root_name = "__data_product__"
            lineage_nodes.append({
                "name": root_name,
                "label": str(product_label),
                "title": str(product_label),
                "layer": 5,
                "upstreams": published_node_names,
                "assoc_upstreams": [],
                "slug": "resumen",
                "num": 1,
                "is_factcube": False,
                "runtime": "object_store",
                "subtext": "Data Product",
            })
            return _catalog_lineage_svg(lineage_nodes, True, {n["name"] for n in lineage_nodes})

    def esc(value: str) -> str:
        return html.escape(str(value or ""), quote=True)

    H, VGAP, HEAD = 42, 14, 28
    left_x, right_x = 22, 360
    left_w, right_w = 300, 250
    rows = max(1, len(entities))
    height = HEAD + rows * (H + VGAP) - VGAP + 8
    width = right_x + right_w + 24
    product_y = HEAD + max(0, (rows - 1) * (H + VGAP) / 2)
    product_label = getattr(product, "business_name", "") or getattr(product, "title", "") or getattr(product, "product_id", "")
    p = [
        f'<div class="catalog-lineage__scroll"><svg class="lineage-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="Linaje del Data Product">',
        '<defs><marker id="dp-ln-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" class="ln-arrowhead"/></marker></defs>',
        '<text x="22" y="12" class="ln-col-label">ENTIDADES PUBLICADAS</text>',
        '<text x="360" y="12" class="ln-col-label">DATA PRODUCT</text>',
    ]
    for i, ent in enumerate(entities):
        y = HEAD + i * (H + VGAP)
        slug = _catalog_data_product_section_slug(ent["display"], i)
        label = ent["display"]
        p.append(f'<path d="M{left_x + left_w},{y + H / 2:.1f} C{left_x + left_w + 45},{y + H / 2:.1f} {right_x - 55},{product_y + H / 2:.1f} {right_x - 4},{product_y + H / 2:.1f}" class="ln-edge" marker-end="url(#dp-ln-arrow)"/>')
        p.append(f'<a href="#{esc(slug)}" class="ln-node-a" data-catalog-anchor="#{esc(slug)}">')
        p.append(f'<rect x="{left_x}" y="{y}" width="{left_w}" height="{H}" rx="8" class="ln-node"/>')
        p.append(f'<text x="{left_x + 16}" y="{y + 26}" class="ln-node-text"><tspan class="ln-num">{i + 2:02d}</tspan> {esc(label[:42] + ("..." if len(label) > 42 else ""))}</text>')
        p.append('</a>')
    p += [
        '<a href="#resumen" class="ln-node-a" data-catalog-anchor="#resumen">',
        f'<rect x="{right_x}" y="{product_y:.1f}" width="{right_w}" height="{H}" rx="8" class="ln-node"/>',
        f'<text x="{right_x + 16}" y="{product_y + 26:.1f}" class="ln-node-text"><tspan class="ln-num">01</tspan> {esc(product_label[:34] + ("..." if len(product_label) > 34 else ""))}</text>',
        '</a>',
        '</svg></div>',
    ]
    return "".join(p)

def _catalog_security_entries():
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_security_catalog(cat_dir)
    except Exception:
        return None, []


def _catalog_security_families() -> list:
    mod, entries = _catalog_security_entries()
    return mod.security_families(entries) if mod else []


def _catalog_security_criteria() -> list:
    mod, entries = _catalog_security_entries()
    return mod.security_criteria(entries) if mod else []


def _catalog_conn_label(conn) -> str:
    return conn.business_name or conn.connection or conn.type_label


def _catalog_md_value(value: str, code: bool = False) -> str:
    value = str(value or "").strip()
    if not value:
        return "-"
    value = value.replace("`", "")
    value = value.replace("|", "\\|")
    if code:
        return f"`{value}`"
    return value


def _catalog_space_sort_key(space: str) -> tuple[int, str]:
    raw = str(space or "").strip()
    norm = unicodedata.normalize("NFKD", raw.upper())
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    order = {
        "DEV": 10, "DES": 10, "DESA": 10, "DESARROLLO": 10, "DEVELOPMENT": 10,
        "QA": 20, "QAS": 20, "CALIDAD": 20, "QUALITY": 20, "TEST": 20,
        "PRD": 30, "PROD": 30, "PRODUCTIVO": 30, "PRODUCCION": 30, "PRODUCTION": 30,
    }
    return (order.get(norm, 100), norm)


def _catalog_connections_svg(nodes: list[dict], spaces: list[str]) -> str:
    if not nodes:
        return ""
    H, VGAP, HEAD = 40, 16, 34
    left_x, ds_x = 28, 610
    NUM_W, CHAR, PAD_R, WMIN, WMAX = 30, 6.3, 14, 170, 360
    ds_w = 260
    spaces = sorted(spaces or ["Datasphere"], key=_catalog_space_sort_key)

    def esc(v: str) -> str:
        return html.escape(str(v or ""), quote=True)

    def _display_label(node: dict) -> str:
        label = str(node.get("label") or "").strip()
        meta = str(node.get("meta") or "").strip()
        return label

    def _node_dims(label: str) -> tuple[int, str]:
        disp = (label[:48] + "...") if len(label) > 51 else label
        w = max(WMIN, min(WMAX, int(NUM_W + len(disp) * CHAR + PAD_R)))
        return w, disp

    def _svg_key(value: str) -> str:
        raw = unicodedata.normalize("NFKD", str(value or ""))
        raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
        raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw.strip().lower()).strip("-")
        return raw or "space"

    space_keys = {space: _svg_key(space) for space in spaces}
    node_dims = [_node_dims(_display_label(n)) for n in nodes]
    row_h = H + VGAP
    top = HEAD + 30
    h = max(230, top + max(len(nodes), len(spaces)) * row_h + 22)
    ds_h = max(154, 64 + len(spaces) * 34)
    ds_y = max(36, (h - ds_h) // 2)
    first_space_y = ds_y + 62
    space_y = {space: first_space_y + i * 34 for i, space in enumerate(spaces)}
    width = max(920, ds_x + ds_w + 28)
    highlight_rules = []
    for key in sorted(set(space_keys.values())):
        hover_focus = f".conn-lineage-svg:has(.conn-space-link--{key}:focus)"
        highlight_rules.append(
            f"{hover_focus} .conn-edge:not(.conn-edge-to-{key}){{opacity:.12;}}"
            f"{hover_focus} .conn-edge-to-{key}{{opacity:1;stroke:#e7b008;stroke-width:2.8;}}"
            f"{hover_focus} .conn-edge-to-{key}.conn-edge--muted{{opacity:.58;}}"
            f"{hover_focus} .conn-space-link--{key} .conn-space{{stroke:#e7b008;stroke-width:1.6;}}"
        )
    highlight_style = "<style>" + "".join(highlight_rules) + "</style>" if highlight_rules else ""

    parts = [
        '<div class="catalog-lineage__scroll">',
        f'<svg class="lineage-svg conn-lineage-svg" viewBox="0 0 {width} {h}" width="{width}" height="{h}" role="img" aria-label="Arquitectura y conectividad del cliente">',
        '<defs><marker id="conn-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" fill="context-stroke"/></marker></defs>',
        highlight_style,
        f'<text x="{left_x}" y="18" class="ln-col-label">Fuentes de Datos</text>',
        f'<text x="{ds_x}" y="18" class="ln-col-label">SAP Datasphere</text>',
        f'<rect x="{ds_x}" y="{ds_y}" width="{ds_w}" height="{ds_h}" rx="8" class="ln-node conn-ds-box"/>',
        f'<text x="{ds_x + ds_w - 16}" y="{ds_y + 30}" text-anchor="end" class="ln-node-text conn-ds-title">Espacios</text>',
    ]
    for space in spaces:
        y = space_y[space]
        skey = space_keys.get(space, _svg_key(space))
        parts.append(f'<g class="conn-space-link conn-space-link--{skey}" tabindex="0" role="button" aria-label="Resaltar conexiones hacia {esc(space)}">')
        parts.append(f'<rect x="{ds_x + 16}" y="{y - 16}" width="{ds_w - 32}" height="28" rx="8" class="ln-node conn-space"/>')
        parts.append(f'<text x="{ds_x + 30}" y="{y + 2}" class="ln-node-text conn-space-text">{esc(space)}</text>')
        parts.append('</g>')
    for idx, (node, (node_w, label)) in enumerate(zip(nodes, node_dims), 1):
        y = top + (idx - 1) * row_h
        cy = y + H / 2
        klass = "conn-node-wrap" + (" conn-node-wrap--muted" if not node.get("selected") else "")
        edge_class = "ln-edge conn-edge" + (" conn-edge--muted" if not node.get("selected") else "")
        href = f'#{esc(node.get("slug", ""))}'
        title = esc(_display_label(node))
        sx = left_x + node_w
        dx = max(32, (ds_x - sx) / 2)
        node_spaces = sorted(node.get("spaces") or [node.get("space") or spaces[0]], key=_catalog_space_sort_key)
        for target_space in node_spaces:
            target_y = space_y.get(target_space, ds_y + ds_h / 2)
            target_key = space_keys.get(target_space, _svg_key(target_space))
            parts.append(f'<path class="{edge_class} conn-edge-to-{target_key}" marker-end="url(#conn-arrow)" d="M{sx},{cy:.0f} C{sx+dx:.0f},{cy:.0f} {ds_x-dx:.0f},{target_y} {ds_x + 16},{target_y}"/>')
        parts.append(f'<a class="ln-node-a conn-node-a" href="{href}"><title>{title}</title><g class="{klass}">')
        parts.append(f'<rect x="{left_x}" y="{y}" width="{node_w}" height="{H}" rx="8" class="ln-node conn-node"/>')
        parts.append(f'<text x="{left_x + 11}" y="{cy + 4:.0f}" class="ln-num">{idx:02d}</text>')
        parts.append(f'<text x="{left_x + 32}" y="{cy + 4:.0f}" class="ln-node-text conn-node-title">{esc(label)}</text>')
        parts.append('</g></a>')
    parts.append('</svg></div>')
    return "".join(parts)


def _catalog_connection_features_text(mod, conn) -> str:
    features = mod.connection_feature_states([conn]) if hasattr(mod, "connection_feature_states") else []
    return " · ".join(
        f"{f['label']} ({'disponible' if f.get('enabled') else 'posible / no disponible'})"
        for f in features
    ) or "-"


def _catalog_conn_norm_key(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def _catalog_conn_lookup(mapping: dict, *keys: str) -> str:
    if not isinstance(mapping, dict):
        return ""
    by_norm = {_catalog_conn_norm_key(k): str(v or "").strip() for k, v in mapping.items()}
    for key in keys:
        value = by_norm.get(_catalog_conn_norm_key(key), "")
        if value and value not in {"—", "-", "None", "none", "null", "?"}:
            return value
    return ""


def _catalog_bool_text(value, default: bool | None = None) -> str:
    if value is None or str(value).strip() == "":
        if default is None:
            return "-"
        return "Sí" if default else "No"
    norm = _catalog_conn_norm_key(str(value))
    if norm in {"si", "true", "1", "on", "yes", "connected", "dpagent"}:
        return "Sí"
    if norm in {"no", "false", "0", "off", "none", "null"}:
        return "No"
    return str(value).strip()


def _catalog_words(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _catalog_has_feature(mod, conn, label: str) -> bool:
    states = mod.connection_feature_states([conn]) if hasattr(mod, "connection_feature_states") else []
    wanted = _catalog_conn_norm_key(label)
    return any(_catalog_conn_norm_key(str(item.get("label") or "")) == wanted for item in states)


def _catalog_is_mssql(conn) -> bool:
    return _catalog_conn_norm_key(getattr(conn, "type_id", "")) == "mssql"


def _catalog_is_generic_jdbc(conn) -> bool:
    """Orígenes JDBC genéricos (GENERICJDBC): PostgreSQL, MySQL, etc. Se detectan por
    typeId o, como respaldo, por `driverClass` (marcador inequívoco de JDBC). NO se usa
    `url` como respaldo: OData también tiene url y caería acá por error."""
    if _catalog_conn_norm_key(getattr(conn, "type_id", "")) == "genericjdbc":
        return True
    info = getattr(conn, "info", {}) or {}
    return bool(_catalog_conn_lookup(info, "driverClass"))


def _catalog_is_odata(conn) -> bool:
    """Orígenes OData (typeId ODATA): conectividad por URL de servicio, no formato SAP."""
    return _catalog_conn_norm_key(getattr(conn, "type_id", "")) == "odata"


def _catalog_connection_rows(mod, conn, include_space: bool = False) -> list[tuple[str, str, str]]:
    info = getattr(conn, "info", {}) or {}
    gateway = getattr(conn, "gateway", {}) or {}
    auth = getattr(conn, "authentication", {}) or {}
    props = getattr(conn, "props", {}) or {}
    feats = getattr(conn, "feature_values", {}) or {}

    def lookup(*keys: str) -> str:
        return (
            _catalog_conn_lookup(info, *keys)
            or _catalog_conn_lookup(props, *keys)
            or _catalog_conn_lookup(feats, *keys)
        )

    rows = [
        ("group", "Identificaci\u00f3n", ""),
    ]
    if include_space:
        rows.append(("row", "Espacio", _catalog_md_value(getattr(conn, "space", ""), True)))
    rows.extend([
        ("row", "Business Name", _catalog_md_value(getattr(conn, "business_name", ""))),
        ("row", "Technical Name", _catalog_md_value(getattr(conn, "connection", ""), True)),
    ])

    if _catalog_is_mssql(conn):
        server = lookup("pds_server_name")
        port = lookup("pds_port_number")
        virtual_host = _catalog_conn_lookup(gateway, "virtualHost")
        virtual_port = _catalog_conn_lookup(gateway, "virtualPort")
        virtual_destination = _catalog_conn_lookup(gateway, "virtual_destination") or lookup("virtual_destination")
        if (
            _catalog_conn_lookup(gateway, "cloudConnectorLocation")
            and virtual_host
            and virtual_port
            and virtual_host == server
            and virtual_port == port
        ):
            virtual_destination_label = "Derivar de conectividad"
        else:
            virtual_destination_label = _catalog_bool_text(virtual_destination)
        rows.extend([
            ("group", "Conectividad", ""),
            ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
            ("row", "Servidor", _catalog_md_value(server, True)),
            ("row", "Puerto", _catalog_md_value(port, True)),
            ("row", "Nombre de base de datos", _catalog_md_value(lookup("pds_database_name"), True)),
            ("row", "Version", _catalog_md_value(lookup("version"))),
            ("group", "Cloud Connector", ""),
            ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
            ("row", "Location ID", _catalog_md_value(_catalog_conn_lookup(gateway, "cloudConnectorLocation"), True)),
            ("row", "Virtual Destination", _catalog_md_value(virtual_destination_label)),
            ("group", "Features", ""),
            ("row", "Remote tables", _catalog_bool_text(lookup("remoteTables"), _catalog_has_feature(mod, conn, "Remote Tables"))),
            ("row", "Data Provisioning Agent", _catalog_md_value(lookup("dpAgent") or getattr(conn, "agent", ""), True)),
            ("row", "Data flows", _catalog_bool_text(lookup("dataflows"), _catalog_has_feature(mod, conn, "Data Flows"))),
            ("row", "Replication Flows", _catalog_bool_text(lookup("replicationflows"), _catalog_has_feature(mod, conn, "Replication Flows"))),
        ])
        return rows

    if _catalog_is_generic_jdbc(conn):
        # Origen JDBC genérico (PostgreSQL, MySQL, …): la conectividad se describe con
        # driverClass + url (no con los campos SAP host/mandante/sysid). El acceso a
        # datos vive en adapterName (Data Provisioning) y kind (Data Access).
        cred_mode = _catalog_conn_lookup(props, "Modo credencial", "credentialMode")
        adapter_name = getattr(conn, "adapter", "") or lookup("adapterName", "adapter")
        kind = _catalog_conn_lookup(info, "kind")
        rows.extend([
            ("group", "Conectividad", ""),
            ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
            ("row", "JDBC driver class", _catalog_md_value(lookup("driverClass"), True)),
            ("row", "JDBC URL", _catalog_md_value(lookup("url"), True)),
            ("group", "Cloud Connector", ""),
            ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
            ("group", "Autenticación", ""),
            ("row", "Modo credencial", _catalog_md_value(cred_mode)),
            ("group", "Remote Tables", ""),
            ("row", "Data Provisioning", _catalog_md_value(adapter_name, True)),
            ("row", "Data Access", _catalog_md_value(kind, True)),
            ("row", "Data Provisioning Agent", _catalog_md_value(lookup("dpAgent") or getattr(conn, "agent", ""), True)),
            ("row", "Estado", _catalog_md_value(getattr(conn, "agent_status", ""))),
        ])
        # Replication Flows: solo si la conexión declara la capacidad. Cuando FEATURES
        # trae REPLICATION FLOW = No, el grupo (con Fast Serialization) se omite.
        if _catalog_has_feature(mod, conn, "Replication Flows"):
            rows.extend([
                ("group", "Replication Flows", ""),
                ("row", "Fast Serialization", _catalog_bool_text(_catalog_conn_lookup(feats, "useFastSerialization"))),
            ])
        rows.extend([
            ("group", "Features", ""),
            ("row", "Remote tables", "Sí" if _catalog_has_feature(mod, conn, "Remote Tables") else "No"),
            ("row", "Data flows", "Sí" if _catalog_has_feature(mod, conn, "Data Flows") else "No"),
            ("row", "Replication Flows", "Sí" if _catalog_has_feature(mod, conn, "Replication Flows") else "No"),
        ])
        return rows

    if _catalog_is_odata(conn):
        # Origen OData: la conectividad es la URL del servicio + versión (no los campos
        # SAP host/mandante/sysid). Cloud Connector con Virtual Destination condicional.
        # Sin grupos Remote Tables / Replication Flows (no aplican al patrón OData).
        auth_odata = _catalog_conn_lookup(info, "authentication") or _catalog_conn_lookup(auth, "auth_type")
        rows.extend([
            ("group", "Conectividad", ""),
            ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
            ("row", "URL", _catalog_md_value(_catalog_conn_lookup(info, "url"), True)),
            ("row", "Version", _catalog_md_value(_catalog_conn_lookup(info, "version"))),
            ("group", "Cloud Connector", ""),
            ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
            ("row", "Location ID", _catalog_md_value(_catalog_conn_lookup(gateway, "cloudConnectorLocation"), True)),
        ])
        vdest = _catalog_bool_text(_catalog_conn_lookup(gateway, "virtual_destination"))
        rows.append(("row", "Virtual Destination", vdest))
        if vdest == "Sí":   # solo si hay virtual destination tiene sentido host/puerto
            rows.extend([
                ("row", "Virtual Host", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualHost"), True)),
                ("row", "Virtual Port", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualPort"), True)),
            ])
        rows.extend([
            ("group", "Autenticación", ""),
            ("row", "Tipo", _catalog_md_value(auth_odata)),
            ("group", "Features", ""),
            ("row", "Remote tables", "Sí" if _catalog_has_feature(mod, conn, "Remote Tables") else "No"),
            ("row", "Data flows", "Sí" if _catalog_has_feature(mod, conn, "Data Flows") else "No"),
            ("row", "Replication Flows", "Sí" if _catalog_has_feature(mod, conn, "Replication Flows") else "No"),
        ])
        return rows

    auth_type = _catalog_conn_lookup(auth, "auth_type")
    data_provisioning = _catalog_conn_lookup(feats, "dataProvisioningOption") or getattr(conn, "data_provisioning", "")
    data_access = _catalog_words(_catalog_conn_lookup(feats, "dataAccess"))
    dp_agent = _catalog_conn_lookup(feats, "dpAgent") or getattr(conn, "agent", "")
    sac_tunnel = _catalog_conn_lookup(feats, "sac_tunnel_connection_name")

    rows.extend([
        ("group", "Conectividad", ""),
        ("row", "Connection type", _catalog_md_value(getattr(conn, "type_id", "") or getattr(conn, "type_label", ""))),
        ("row", "Modo de conexión", _catalog_md_value(_catalog_conn_lookup(info, "connectionType"))),
        ("row", "Application Server", _catalog_md_value(_catalog_conn_lookup(info, "host"), True)),
        ("row", "System Number", _catalog_md_value(_catalog_conn_lookup(info, "systemnumber"), True)),
        ("row", "Mandante", _catalog_md_value(_catalog_conn_lookup(info, "client"), True)),
        ("row", "ID", _catalog_md_value(_catalog_conn_lookup(info, "sysid"), True)),
        ("row", "Idioma", _catalog_md_value(_catalog_conn_lookup(info, "langcode"), True)),
        ("group", "Cloud Connector", ""),
        ("row", "Configurado", _catalog_bool_text(_catalog_conn_lookup(gateway, "cloud_connector"), getattr(conn, "cloud_connector", False))),
        ("row", "Location ID", _catalog_md_value(_catalog_conn_lookup(gateway, "cloudConnectorLocation"), True)),
        ("row", "Virtual Destination", _catalog_bool_text(_catalog_conn_lookup(gateway, "virtual_destination"))),
        ("row", "Virtual Host", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualHost"), True)),
        ("row", "Virtual Port", _catalog_md_value(_catalog_conn_lookup(gateway, "virtualPort"), True)),
        ("group", "Autenticaci\u00f3n", ""),
        ("row", "Tipo", _catalog_md_value(auth_type)),
        ("group", "Remote Tables", ""),
        ("row", "Data Provisioning", _catalog_md_value(data_provisioning)),
        ("row", "Data Access", _catalog_md_value(data_access)),
        ("row", "Data Provisioning Agent", _catalog_md_value(dp_agent, True)),
        ("row", "Estado", _catalog_md_value(getattr(conn, "agent_status", ""))),
        ("row", "Streaming Read", _catalog_bool_text(_catalog_conn_lookup(feats, "streaming"))),
        ("group", "Replication Flows", ""),
        ("row", "Fast Serialization", _catalog_bool_text(_catalog_conn_lookup(feats, "useFastSerialization"))),
        ("group", "Features", ""),
        ("row", "Remote tables", "S\u00ed" if _catalog_has_feature(mod, conn, "Remote Tables") else "No"),
        ("row", "Data flows", "S\u00ed" if _catalog_has_feature(mod, conn, "Data Flows") else "No"),
        ("row", "Replication Flows", "S\u00ed" if _catalog_has_feature(mod, conn, "Replication Flows") else "No"),
        ("row", "Model Import", "S\u00ed" if sac_tunnel else "No"),
    ])
    return rows


def _catalog_connection_table_md(mod, conns: list) -> list[str]:
    if not conns:
        return []
    if len(conns) == 1:
        lines = ["| Propiedad | Valor |", "|---|---|"]
        for kind, label, value in _catalog_connection_rows(mod, conns[0], include_space=True):
            if kind == "group":
                lines.append(f"| {{{{table_group: {label}}}}} | |")
            else:
                lines.append(f"| {label} | {value or '-'} |")
        lines.append("")
        return lines

    headers = [_catalog_md_value(conn.space, True) for conn in conns]
    rows_by_conn = [_catalog_connection_rows(mod, conn) for conn in conns]
    lines = ["| Propiedad | " + " | ".join(headers) + " |", "|---|" + "|".join("---" for _ in conns) + "|"]
    for idx, (kind, label, _value) in enumerate(rows_by_conn[0]):
        if kind == "group":
            lines.append("| {{table_group: " + label + "}} | " + " | ".join("" for _ in conns) + " |")
        else:
            values = [rows[idx][2] if idx < len(rows) else "-" for rows in rows_by_conn]
            lines.append("| " + label + " | " + " | ".join(value or "-" for value in values) + " |")
    lines.append("")
    return lines



def _catalog_compliance_intro_md(verdict: str, verdict_text: str, scope: str) -> str:
    label = _catalog_verdict_label(verdict)
    context = str(scope or "la evidencia disponible").strip()
    return (
        f"> Esta lectura consolida los hallazgos detectados sobre {context} y explica por qué "
        f"el catálogo lo clasifica como **{label} · {verdict_text}**."
    )


def _catalog_connection_compliance_html(mod, detail: dict) -> str:
    """Build a deterministic compliance table for Connections & Spaces detail."""
    connections = list(detail.get("connections") or [])
    selected_paths = set(detail.get("selected_paths") or set())
    selected = [conn for conn in connections if not selected_paths or conn.path in selected_paths] or connections
    if not selected:
        return ""

    verdict = str(detail.get("verdict") or "parcial").strip().lower()
    verdict_label = _catalog_verdict_label(verdict)
    verdict_text = _catalog_verdict_text(verdict)
    type_label = str(detail.get("type_label") or detail.get("type_id") or "tipo de conexión")
    count = int(detail.get("count") or len(selected))
    lines = [
        f"- {verdict} **Dictamen del catálogo** _Evidencia:_ {verdict_label} — {verdict_text}; {count} "
        f"{'conexión evaluada' if count == 1 else 'conexiones evaluadas'} para {type_label}."
    ]

    spaces = sorted({str(getattr(conn, "space", "") or "").strip() for conn in selected if getattr(conn, "space", "")}, key=_catalog_space_sort_key)
    business_names = {str(_catalog_conn_label(conn) or "").strip() for conn in selected}
    technical_names = {str(getattr(conn, "connection", "") or "").strip() for conn in selected}
    if len(selected) == 1:
        space_text = spaces[0] if spaces else "el espacio informado"
        lines.append(
            f"- nota **Cobertura por espacio** _Evidencia:_ La conexión está documentada para {space_text}; "
            "no requiere comparación entre ambientes."
        )
    elif len(business_names) == 1 and len(technical_names) == 1:
        lines.append(
            f"- cumple **Consistencia entre espacios** _Evidencia:_ Business Name y Technical Name se mantienen alineados en "
            f"{' | '.join(spaces)}."
        )
    else:
        details = []
        if len(business_names) > 1:
            details.append("Business Name no homogéneo")
        if len(technical_names) > 1:
            details.append("Technical Name no homogéneo")
        lines.append(
            f"- observar **Consistencia entre espacios** _Evidencia:_ {'; '.join(details) or 'hay diferencias de metadata'} "
            f"para {' | '.join(spaces) or 'los espacios evaluados'}."
        )

    try:
        feature_states = mod.connection_feature_states(selected) if mod else []
    except Exception:
        feature_states = []
    enabled = [str(item.get("label") or "") for item in feature_states if item.get("enabled")]
    unavailable = [str(item.get("label") or "") for item in feature_states if not item.get("enabled")]
    if enabled and not unavailable:
        lines.append(
            f"- cumple **Capacidades disponibles** _Evidencia:_ {' | '.join(enabled)} disponibles según configuración de agente, "
            "Cloud Connector y features declaradas."
        )
    elif enabled:
        lines.append(
            f"- observar **Capacidades disponibles** _Evidencia:_ Disponibles: {' | '.join(enabled)}. "
            f"No disponibles o pendientes: {' | '.join(unavailable) or 'sin detalle'}."
        )
    else:
        lines.append(
            "- atender **Capacidades disponibles** _Evidencia:_ No se detectaron capacidades operativas disponibles con la metadata evaluada."
        )

    additional = [conn for conn in connections if selected_paths and conn.path not in selected_paths]
    if additional:
        labels = sorted({f"{getattr(conn, 'space', '')} · {getattr(conn, 'connection', '')}" for conn in additional})
        lines.append(
            f"- observar **Cobertura fuera del origen seleccionado** _Evidencia:_ Existen conexiones adicionales atenuadas para comparación: "
            f"{' | '.join(labels)}."
        )

    return _catalog_compliance_table_html("\n".join(lines))


def _catalog_connection_detail_view(mod, detail: dict, pack: str = ""):
    from webapp.services.content_service import render_sections

    selected_paths = detail.get("selected_paths") or set()
    grouped: dict[tuple, dict] = {}
    for conn in detail.get("connections") or []:
        selected = conn.path in selected_paths
        key = (selected, conn.type_id, conn.connection, _catalog_conn_label(conn))
        group = grouped.setdefault(key, {
            "conns": [],
            "selected": selected,
            "label": f"{conn.connection} \u00b7 {_catalog_conn_label(conn)}",
            "meta": conn.type_label,
        })
        group["conns"].append(conn)

    groups = sorted(
        grouped.values(),
        key=lambda g: (
            not g.get("selected"),
            _catalog_space_sort_key((g.get("conns") or [None])[0].space if g.get("conns") else ""),
            str(g.get("label") or "").lower(),
        ),
    )

    parts: list[str] = []
    for group in groups:
        conns = sorted(group["conns"], key=lambda c: _catalog_space_sort_key(c.space))
        first = conns[0]
        origin_label = _catalog_conn_label(first)
        heading = f"## {{dw-black}}{first.connection} \u00b7 {{dw-gold}}{origin_label}"
        body_parts = [heading, ""]
        count = len(conns)
        if count > 1:
            spaces_word = "espacio distinto" if count == 1 else "espacios distintos"
            body_parts.append(
                f"> El origen {origin_label} ({first.connection}) conecta contra {count} {spaces_word} en Datasphere; "
                "a continuación desglosamos el detalle técnico en cada caso."
            )
            body_parts.append("")
        if not group["selected"]:
            body_parts.append(
                "> Conexión adicional fuera del origen seleccionado. Se muestra por tener activado _Incluir todas las conexiones_."
            )
            body_parts.append("")

        body_parts.extend(_catalog_connection_table_md(mod, conns))
        parts.append("\n".join(body_parts).strip())

    compliance_html = _catalog_connection_compliance_html(mod, detail)
    if compliance_html:
        verdict = str(detail.get("verdict") or "parcial").strip().lower()
        intro = _catalog_compliance_intro_md(
            verdict,
            _catalog_verdict_text(verdict),
            f"las conexiones y espacios de {detail.get('cliente') or 'este cliente'} para {detail.get('type_label') or detail.get('type_id') or 'este tipo de conexión'}",
        )
        parts.append("## Cumplimiento\n\n" + intro + "\n\n" + compliance_html)

    composed = "\n\n".join(parts)
    sections = render_sections(composed, pack_name="", stem="", base=0) if composed else []
    nodes = []
    for group, section in zip(groups, sections):
        node_spaces = []
        for conn in sorted(group["conns"], key=lambda c: _catalog_space_sort_key(c.space)):
            if conn.space and conn.space not in node_spaces:
                node_spaces.append(conn.space)
        nodes.append({
            "slug": section.slug,
            "spaces": node_spaces,
            "label": group["label"],
            "meta": group["meta"],
            "selected": group["selected"],
        })
    svg = _catalog_connections_svg(nodes, detail.get("spaces") or [])
    return sections, svg


@app.get("/catalog/connections", response_class=HTMLResponse, include_in_schema=False)
def catalog_connections(request: Request, type_id: str = "", pack: str = Query(DEFAULT_PACK)):
    # Catálogo operativo de arquitectura/conectividad: conexiones agrupadas por cliente
    # según typeId Datasphere. Determinístico y sin tokens.
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    mod, entries = _catalog_architecture_connections()
    hits = mod.connections_for_type(entries, type_id) if (mod and type_id) else []
    groups = mod.connection_client_groups(hits) if mod else []
    label = mod.connection_type_label(type_id) if (mod and type_id) else ""
    return templates.TemplateResponse(
        request=request, name="partials/catalog_connections.html",
        context={"groups": groups, "type_id": type_id, "type_label": label, "pack": _pack(pack)},
    )


@app.get("/catalog/connections/detail", response_class=HTMLResponse, include_in_schema=False)
def catalog_connection_detail(
    request: Request,
    cliente: str = "",
    type_id: str = "",
    pack: str = Query(DEFAULT_PACK),
    all: int = 0,
):
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_architecture_connections()
    detail = mod.connection_client_detail(entries, cliente, type_id, bool(all)) if mod else None
    if detail is None:
        raise HTTPException(404, "Detalle de conexion no encontrado en el catalogo")
    sections, lineage_svg = _catalog_connection_detail_view(mod, detail, selected)
    context = {
        "pack": selected,
        "cliente": detail["cliente"],
        "type_id": detail["type_id"],
        "type_label": detail["type_label"],
        "include_all": detail["include_all"],
        "count": detail["count"],
        "all_count": detail["all_count"],
        "verdict": detail["verdict"],
        "verdict_label": detail["verdict_label"],
        "lineage_svg": lineage_svg,
        "sections": sections,
        "catalog_kind": "connections",
        "catalog_ref": f"{detail['cliente']} · {detail['type_label']}",
    }
    # HTMX → solo el fragmento (dentro de la pestaña Catálogo del módulo 04, sin
    # perder el panel). Navegación directa (deep-link del asistente) → página
    # completa con shell + nav, igual que /catalog/flow.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request=request, name="partials/catalog_connection_content.html", context=context,
        )
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    context.update({
        "modules": list_modules(selected),
        "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
        "academy": academy,
        "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
        "active_group": "", "active_stem": "",
        "readonly": _readonly(request), "user": user,
        "pack_label": content_pack_short_label(selected),
    })
    return templates.TemplateResponse(
        request=request, name="catalog_connection.html", context=context,
    )





def _catalog_security_lineage_svg(entry, show_lineage: bool = False) -> str:
    """SVG liviano para Seguridad/DAC: entidad de permisos -> DAC -> vistas consumidoras."""
    from core.utils import slugify

    def _section_slug_for(needle: str) -> str:
        wanted = str(needle or "").strip()
        if not wanted:
            return ""
        for line in (getattr(entry, "body", "") or "").splitlines():
            if not line.lstrip().startswith("##"):
                continue
            title = re.sub(r"^#+\s*", "", line).strip()
            title = re.sub(r"\{dw-[^}]+\}", "", title, flags=re.I)
            title = title.replace("\\_", "_").replace("**", "")
            if wanted in title:
                return slugify(title)
        return slugify(wanted)

    source_raw = entry.source_entity or "Entidad de permisos"
    source = html.escape(source_raw)
    source_slug = html.escape(_section_slug_for(source_raw))
    dac_raw = entry.dac or "DAC"
    dac = html.escape(dac_raw)
    dac_slug = html.escape(_section_slug_for(dac_raw))
    views_raw = getattr(entry, "lineage_views", []) or []
    views = []
    seen = set()
    for row in views_raw:
        view = str((row or {}).get("view") or "").strip()
        space = str((row or {}).get("space") or "").strip()
        if not view:
            continue
        key = (space, view)
        if key in seen:
            continue
        seen.add(key)
        views.append({"space": space, "view": view})
    visible_views = views[:8] if show_lineage else []
    w = 940
    left_x, mid_x, right_x = 36, 352, 668
    node_w, node_h = 260, 50
    top_y = 78
    rows = max(1, len(visible_views))
    h = max(210, top_y + rows * 62 + 42)
    source_y = top_y + max(0, rows - 1) * 31
    dac_y = source_y

    parts = [
        '<div class="catalog-lineage__scroll">',
        f'<svg class="lineage-svg security-lineage-svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="Linaje de seguridad DAC">',
        '<defs><marker id="sec-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L8,4 L0,8 Z" class="ln-arrowhead"/></marker></defs>',
        f'<text x="{left_x}" y="24" class="ln-col-label">Entidad de Permisos</text>',
        f'<text x="{mid_x}" y="24" class="ln-col-label">Objeto DAC</text>',
    ]
    if show_lineage:
        parts.append(f'<text x="{right_x}" y="24" class="ln-col-label">Linaje consumidor</text>')
    source_open = f'<a href="#{source_slug}" class="ln-node-link" aria-label="Abrir entidad de permisos {source}">' if source_slug else ''
    source_close = '</a>' if source_slug else ''
    dac_open = f'<a href="#{dac_slug}" class="ln-node-link" aria-label="Abrir objeto DAC {dac}">' if dac_slug else ''
    dac_close = '</a>' if dac_slug else ''
    parts.extend([
        source_open,
        f'<rect x="{left_x}" y="{source_y}" width="{node_w}" height="{node_h}" rx="8" class="ln-node"/>',
        f'<text class="ln-num" x="{left_x+14}" y="{source_y+28}">01</text>',
        f'<text class="ln-node-text" x="{left_x+38}" y="{source_y+28}">{source}</text>',
        source_close,
        dac_open,
        f'<rect x="{mid_x}" y="{dac_y}" width="{node_w}" height="{node_h}" rx="8" class="ln-node security-dac-node"/>',
        f'<text class="ln-num" x="{mid_x+14}" y="{dac_y+28}">02</text>',
        f'<text class="ln-node-text" x="{mid_x+38}" y="{dac_y+28}">{dac}</text>',
        dac_close,
        f'<path class="ln-edge" d="M{left_x+node_w},{source_y+node_h/2:.0f} C{left_x+node_w+70},{source_y+node_h/2:.0f} {mid_x-70},{dac_y+node_h/2:.0f} {mid_x},{dac_y+node_h/2:.0f}" marker-end="url(#sec-arrow)"/>',
    ])
    if show_lineage and visible_views:
        for idx, row in enumerate(visible_views, start=1):
            y = top_y + (idx - 1) * 62
            label = html.escape(row["view"][:40] + ("..." if len(row["view"]) > 40 else ""))
            space = html.escape(row["space"])
            parts.extend([
                f'<rect x="{right_x}" y="{y}" width="{node_w}" height="{node_h}" rx="8" class="ln-node security-view-node"/>',
                f'<text class="ln-num" x="{right_x+14}" y="{y+22}">{idx+2:02d}</text>',
                f'<text class="ln-node-text" x="{right_x+38}" y="{y+22}">{label}</text>',
                f'<text class="ln-node-subtext" x="{right_x+38}" y="{y+39}">{space}</text>',
                f'<path class="ln-edge ln-edge--assoc" d="M{mid_x+node_w},{dac_y+node_h/2:.0f} C{mid_x+node_w+72},{dac_y+node_h/2:.0f} {right_x-72},{y+node_h/2:.0f} {right_x},{y+node_h/2:.0f}" marker-end="url(#sec-arrow)"/>',
            ])
        if len(views) > len(visible_views):
            parts.append(f'<text x="{right_x}" y="{top_y + len(visible_views) * 62 + 18}" class="ln-node-subtext">+ {len(views) - len(visible_views)} vistas adicionales en el detalle</text>')
    elif show_lineage:
        parts.append(f'<text x="{right_x}" y="{dac_y+28}" class="ln-node-subtext">Sin vistas consumidoras informadas</text>')
    parts.append('</svg></div>')
    return ''.join(parts)


def _catalog_roles_svg(entry, sections) -> str:
    """SVG de COBERTURA de roles (Seguridad de Roles). Se arma DESDE las secciones
    renderizadas (el CONTENIDO dicta el SVG): un nodo por sección de rol, con el MISMO
    número [NN] y la MISMA ancla que la sección — nunca muestra un rol que no esté en el
    contenido. Bandas = capas de Datasphere (según la función del rol), columnas =
    entornos DEV/QA/PRD. Cada rol abarca los entornos de su alcance; los globales abarcan
    la ENVOLVENTE de su capa (unión de scopes de los de alcance) → Modelado/Integración
    no llegan a Producción. Capa/scope salen del frontmatter `roles` (solo lookup)."""
    meta = {str(r.get("id")): r for r in (getattr(entry, "roles", []) or []) if r.get("id")}
    if not meta:
        return ""
    ENVS = ["DEV", "QA", "PRD"]
    LAYERS = ["Integración", "Modelado", "Consumo", "Gobierno y Administración"]
    CLS = {"Integración": "int", "Modelado": "mod", "Consumo": "con", "Gobierno y Administración": "gov"}
    # Nodos = secciones de rol realmente renderizadas (título "role_id · Matriz…").
    nodes = []
    for s in sections:
        rid = str(getattr(s, "title", "")).split("·")[0].strip()
        m = meta.get(rid)
        if not m:
            continue
        nodes.append({**m, "num": getattr(s, "number", 0), "slug": getattr(s, "slug", "")})
    if not nodes:
        return ""
    by_layer = {l: [] for l in LAYERS}
    for n in nodes:
        lyr = n.get("layer") if n.get("layer") in by_layer else "Gobierno y Administración"
        by_layer[lyr].append(n)

    def _sc(n):
        return {e for e in ENVS if e in (n.get("scopes") or [])}
    layer_envs = {}
    for l in LAYERS:
        acc = set()
        for n in by_layer[l]:
            if n.get("category") != "global":
                acc |= _sc(n)
        layer_envs[l] = acc or set(ENVS)

    COLW, X0 = 208, 210
    W = X0 + len(ENVS) * COLW + 14
    RH, RGAP, BANDPAD, HEADER = 34, 10, 40, 52
    col_x = lambda i: X0 + i * COLW
    bands, content = [], []
    y = HEADER
    for l in LAYERS:
        rs = by_layer[l]
        if not rs:
            continue
        c = CLS[l]
        bh = BANDPAD + len(rs) * (RH + RGAP)
        bands.append(f'<rect x="8" y="{y}" width="{W-16}" height="{bh-10}" rx="10" class="rl-band rl-band--{c}"/>')
        content.append(f'<text x="18" y="{y+23}" class="rl-lyr rl-lyr--{c}">{html.escape(l)}</text>')
        ry = y + BANDPAD - 4
        for n in rs:
            glob = n.get("category") == "global"
            sc = layer_envs[l] if glob else _sc(n)
            idxs = sorted(ENVS.index(e) for e in sc) or [0, 1, 2]
            xa = col_x(idxs[0]) + 6
            xb = col_x(idxs[-1]) + COLW - 12
            rid = html.escape(str(n.get("id") or ""))
            badge = "GLOBAL" if glob else " · ".join(e for e in ENVS if e in sc)
            slug = html.escape(str(n.get("slug") or ""))
            num = int(n.get("num") or 0)
            cy = ry + RH / 2 + 4
            badge_cls = "ln-node-subtext rl-badge-global" if glob else "ln-node-subtext"
            open_a = f'<a href="#{slug}" class="ln-node-a rl-role">' if slug else '<g class="rl-role">'
            close_a = '</a>' if slug else '</g>'
            content.append(
                f'{open_a}'
                f'<rect class="ln-node" x="{xa}" y="{ry}" width="{max(80, xb-xa)}" height="{RH}" rx="8"/>'
                f'<text class="ln-num" x="{xa+12}" y="{cy:.0f}">{num:02d}</text>'
                f'<text class="ln-node-text" x="{xa+32}" y="{cy:.0f}">{rid}</text>'
                f'<text class="{badge_cls}" x="{xb-12}" y="{cy:.0f}" text-anchor="end">{html.escape(badge)}</text>'
                f'{close_a}')
            ry += RH + RGAP
        y += bh
    H = y + 12
    heads = ['<text x="18" y="34" class="rl-collabel">CAPA · ENTORNO</text>']
    guides = []
    for i, e in enumerate(ENVS):
        cx, cw = col_x(i), COLW - 12
        heads.append(f'<rect x="{cx+6}" y="15" width="{cw}" height="26" rx="6" class="rl-envhead"/>')
        heads.append(f'<text x="{cx+6+cw/2:.0f}" y="33" class="rl-env" text-anchor="middle">{e}</text>')
        guides.append(f'<line x1="{cx+6}" y1="47" x2="{cx+6}" y2="{H-8}" class="rl-guide"/>')
    guides.append(f'<line x1="{col_x(len(ENVS)-1)+COLW-6}" y1="47" x2="{col_x(len(ENVS)-1)+COLW-6}" y2="{H-8}" class="rl-guide"/>')
    svg = (f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}" class="roles-svg lineage-svg" '
           f'role="img" aria-label="Cobertura de roles por capa y entorno">'
           + "".join(bands) + "".join(guides) + "".join(heads) + "".join(content) + "</svg>")
    return '<div class="catalog-lineage__scroll">' + svg + '</div>'


@app.get("/catalog/security", response_class=HTMLResponse, include_in_schema=False)
def catalog_security(request: Request, family: str = "", pack: str = Query(DEFAULT_PACK)):
    # Catálogo operativo de Seguridad: familias funcionales (roles / datos).
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    mod, entries = _catalog_security_entries()
    hits = mod.security_for_family(entries, family) if (mod and family) else []
    family_label = ""
    if mod and family:
        family_label = next((f["label"] for f in mod.security_families(entries) if f["value"] == family), family)
    return templates.TemplateResponse(
        request=request,
        name="partials/catalog_security.html",
        context={"items": hits, "family": family, "family_label": family_label, "pack": _pack(pack)},
    )


@app.get("/catalog/security/detail", response_class=HTMLResponse, include_in_schema=False)
def catalog_security_detail(
    request: Request,
    cliente: str = "",
    criterio: str = "",
    dac: str = "",
    pack: str = Query(DEFAULT_PACK),
    lineage: int = 0,
):
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_security_entries()
    entry = mod.find_security_entry(entries, cliente, criterio, dac) if mod else None
    if entry is None:
        raise HTTPException(404, "Detalle de seguridad no encontrado en el catálogo")
    from webapp.services.content_service import render_sections
    is_roles = getattr(entry, "family", "") == "roles"
    show_lineage = bool(lineage) and not is_roles
    sections = render_sections(entry.body, pack_name=selected, stem=f"catalog_security_{entry.cliente}_{entry.dac}")
    # Roles → SVG de cobertura armado DESDE las secciones (el contenido dicta el SVG);
    # Datos/DAC → linaje consumidor.
    lineage_svg = _catalog_roles_svg(entry, sections) if is_roles else _catalog_security_lineage_svg(entry, show_lineage)
    return templates.TemplateResponse(
        request=request,
        name="partials/catalog_security_content.html",
        context={
            "pack": selected,
            "entry": entry,
            "sections": sections,
            "lineage_svg": lineage_svg,
            "show_lineage": show_lineage,
            "catalog_kind": "security",
            "catalog_ref": f"{entry.cliente} · {entry.criterio}",
        },
    )

_WHATSNEW_COOKIE_MAXAGE = 60 * 60 * 24 * 365


def _whatsnew_mark_seen(response, hash_: str) -> None:
    if hash_:
        response.set_cookie(
            whatsnew.COOKIE_NAME, hash_, max_age=_WHATSNEW_COOKIE_MAXAGE, path="/",
            httponly=False, samesite=SESSION_COOKIE_SAMESITE, secure=SESSION_COOKIE_SECURE,
        )


@app.get("/whats-new", response_class=HTMLResponse, include_in_schema=False)
def whats_new_page(request: Request, pack: str = Query(DEFAULT_PACK)):
    """Página de Novedades: los commits marcados con trailer `Novedad:`, renderizados
    con el pipeline de contenido. Visitarla marca todo como visto (avanza la cookie)."""
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    items = whatsnew.entries(selected)
    categorias = sorted({e["categoria"] for e in items if e.get("categoria")})
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    resp = templates.TemplateResponse(
        request=request, name="whats_new.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request), "pack": selected,
            "pack_label": content_pack_short_label(selected), "user": user,
            # El detalle lo arma Alpine desde este JSON (filtros + colapsables por fecha).
            "whatsnew_items": items, "categorias": categorias, "n_novedades": len(items),
        },
    )
    _whatsnew_mark_seen(resp, items[0]["ref"] if items else "")
    return resp


@app.post("/whats-new/dismiss", response_class=HTMLResponse, include_in_schema=False)
def whats_new_dismiss(request: Request):
    """Cierra el banner y no lo vuelve a mostrar hasta que haya una novedad más nueva
    (avanza la cookie al anuncio más reciente). Devuelve vacío → HTMX quita el banner."""
    resp = HTMLResponse("")
    _whatsnew_mark_seen(resp, whatsnew.latest_ref())
    return resp


@app.get("/catalog/domain", response_class=HTMLResponse, include_in_schema=False)
def catalog_domain(request: Request, dominio: str = "", modulo: str = "", pack: str = Query(DEFAULT_PACK)):
    # Determinístico y sin tokens → disponible para cualquier usuario logueado
    # (incluido el perfil de solo lectura), como Árbol/Diagnóstico.
    if not auth_service.session_user(request):
        raise HTTPException(401, "Sin sesión")
    mod, entries = _catalog_entries()
    flows = mod.flows_for_domain(entries, dominio) if (mod and dominio) else []
    # Énfasis por módulo (mismo catálogo global; solo cambia qué se resalta):
    # 03 Integración → fuentes de extracción; 04 Modelado → capas.
    foco = {"03": "fuentes"}.get((modulo or "").strip())
    return templates.TemplateResponse(
        request=request, name="partials/catalog_flows.html",
        context={"flows": flows, "dominio": dominio, "foco": foco, "pack": _pack(pack)},
    )


_CATALOG_LAYERS = ["Ingesta", "Armonización", "Propagación", "Consumo"]


def _catalog_layer_index(auto: dict, name: str) -> int:
    """Capa (0-3) de un artefacto para el diagrama de linaje. Confía en `auto.capa`
    (que el generador ya computa de forma robusta); si no fuese una capa canónica,
    espeja la misma prioridad: prefijo 1-4 → esa capa; remoto → Ingesta; patrón de
    modelado (dimensión→Armonización, hecho→Propagación, cubo→Consumo); AM/EM→Consumo."""
    capa = auto.get("capa")
    if capa in _CATALOG_LAYERS:
        return _CATALOG_LAYERS.index(capa)
    if name[:1] in "1234":
        return int(name[0]) - 1
    if auto.get("origen_remoto"):
        return 0
    sem = f"{auto.get('data_category') or ''} {auto.get('modeling_pattern') or ''}".upper()
    if "CUBE" in sem:
        return 3
    if "FACT" in sem:
        return 2
    if "DIMENSION" in sem:
        return 1
    pref = name.split("_", 1)[0]
    if pref in ("AM", "EM"):
        return 3
    if pref[:1] == "V" or pref in ("VF", "VD", "VR", "VS"):
        return 2
    return 0   # no clasificable → Ingesta (NO Consumo: evita falsas anclas de sistema)


def _catalog_flow_keep(nodes: list, show_assoc: bool) -> set:
    """Conjunto de artefactos VISIBLES del flujo = ancestros DIRIGIDOS del OUTPUT
    (nodos de la capa más profunda CON aristas directas). Padres directos siempre; +
    asociación si `show_assoc`. Excluye ramas laterales (comparten fuente pero no
    alimentan la salida) y artefactos de sistema aislados (SAP.TIME, DAC, …). Lo usan
    tanto el diagrama como el filtro de secciones, para que ambos coincidan."""
    from collections import defaultdict
    names = {n["name"] for n in nodes}
    adj = defaultdict(set)
    dparent, aparent = defaultdict(set), defaultdict(set)
    for n in nodes:
        for up in n["upstreams"]:
            if up in names:
                adj[up].add(n["name"]); adj[n["name"]].add(up); dparent[n["name"]].add(up)
        for up in n["assoc_upstreams"]:
            if up in names:
                aparent[n["name"]].add(up)
    connected = {n["name"] for n in nodes if adj[n["name"]]}
    if not connected:
        return names
    maxlayer = max(n["layer"] for n in nodes if n["name"] in connected)
    anchors = {n["name"] for n in nodes if n["layer"] == maxlayer and n["name"] in connected}
    keep, stack = set(anchors), list(anchors)
    while stack:
        t = stack.pop()
        for up in dparent[t] | (aparent[t] if show_assoc else set()):
            if up not in keep:
                keep.add(up)
                stack.append(up)
    return keep


def _catalog_lineage_svg(nodes: list, show_assoc: bool, keep: set) -> str:
    """Diagrama SVG del linaje del OUTPUT del flujo (solo los nodos en `keep`).
    Aristas DIRECTAS (FROM/JOIN) siempre; por ASOCIACIÓN (dimensiones/textos) solo con
    `show_assoc` (punteadas). Cada nodo es un ancla a su sección; el `num` del nodo ya
    viene alineado con el nº de sección (posición en el orden visible). `nodes`:
    [{name,label,title,layer,upstreams,assoc_upstreams,slug,num,is_factcube}]."""
    from collections import defaultdict
    names = {n["name"] for n in nodes if n["name"] in keep}
    direct_edges = [(up, n["name"]) for n in nodes if n["name"] in keep
                    for up in n["upstreams"] if up in names]
    assoc_edges = [(up, n["name"]) for n in nodes if n["name"] in keep
                   for up in n["assoc_upstreams"] if up in names]
    shown = [n for n in nodes if n["name"] in keep]
    if len(shown) < 2:
        return ""

    # Layout por DEPENDENCIA, criterio ALAP ("as late as possible", pegado al
    # consumo) como Datasphere: la columna se define por la distancia al SINK
    # (consumo final), NO por la distancia a la fuente. Así las fuentes DIRECTAS de
    # un nodo caen en la vertical inmediatamente anterior a él (p. ej. las fuentes de
    # 2VR juntas justo antes de 2VR). La capa de responsabilidad se muestra en el
    # detalle de cada artefacto, no condiciona la posición.
    shown_names = {n["name"] for n in shown}
    edges_layout = direct_edges + (assoc_edges if show_assoc else [])
    down_of = defaultdict(set)   # nodo → sus consumidores (distancia al sink)
    for up, tgt in edges_layout:
        if up in shown_names and tgt in shown_names:
            down_of[up].add(tgt)
    tosink_cache = {}

    def _to_sink(n, seen):
        if n in tosink_cache:
            return tosink_cache[n]
        downs = [d for d in down_of.get(n, ()) if d not in seen]
        r = 0 if not downs else 1 + max(_to_sink(d, seen | {n}) for d in downs)
        tosink_cache[n] = r
        return r

    for n in shown:
        _to_sink(n["name"], frozenset())
    maxlevel = max(tosink_cache.values(), default=0)
    by_col = defaultdict(list)
    for n in shown:
        by_col[maxlevel - tosink_cache[n["name"]]].append(n)   # columna = distancia al sink invertida
    cols = sorted(by_col)
    H, VGAP, COLGAP, HEAD = (46 if any(n.get("subtext") for n in shown) else 40), 16, 44, 6
    # Ancho POR CONTENIDO: cada caja se ajusta a su texto (no ancho fijo), para no
    # desperdiciar espacio; el ancho de columna = el del nodo más ancho de la columna.
    NUM_W, CHAR, PAD_R, WMIN, WMAX = 30, 6.3, 14, 92, 260

    def _node_dims(n):
        disp = (n["label"][:33] + "…") if len(n["label"]) > 34 else n["label"]
        w = max(WMIN, min(WMAX, int(NUM_W + len(disp) * CHAR + PAD_R)))
        return w, disp

    node_w, node_disp, col_width = {}, {}, {}
    for d in cols:
        wmax = 0
        for n in by_col[d]:
            w, disp = _node_dims(n)
            node_w[n["name"]], node_disp[n["name"]] = w, disp
            wmax = max(wmax, w)
        col_width[d] = wmax
    col_x, acc = {}, 0
    for d in cols:
        col_x[d] = acc
        acc += col_width[d] + COLGAP
    pos, max_rows = {}, 0
    is_data_product_lineage = any(str(n.get("runtime") or "") == "object_store" for n in shown) and any(str(n.get("subtext") or "") == "Data Product" for n in shown)
    if is_data_product_lineage:
        hana_cols = [d for d in cols if any(str(n.get("runtime") or "") == "hana" for n in by_col[d])]
        object_cols = [d for d in cols if any(str(n.get("runtime") or "") != "hana" for n in by_col[d])]

        def _band_x(band_cols: list[int]) -> dict[int, int]:
            out: dict[int, int] = {}
            band_acc = 0
            for d in band_cols:
                out[d] = band_acc
                band_acc += col_width[d] + COLGAP
            return out

        hana_x = _band_x(hana_cols)
        object_x = _band_x(object_cols)
        band_pad_x = 16
        band_label_h = 26
        hana_rows = 0
        object_rows = 0
        for d in hana_cols:
            hana_idx = 0
            for n in by_col[d]:
                if str(n.get("runtime") or "") == "hana":
                    pos[n["name"]] = (band_pad_x + hana_x[d], band_label_h + hana_idx * (H + VGAP))
                    hana_idx += 1
            hana_rows = max(hana_rows, hana_idx)

        hana_height = band_label_h + max(1, hana_rows) * (H + VGAP) - VGAP + 18
        object_top = hana_height + 22
        for d in object_cols:
            object_idx = 0
            for n in by_col[d]:
                if str(n.get("runtime") or "") != "hana":
                    pos[n["name"]] = (band_pad_x + object_x[d], object_top + band_label_h + object_idx * (H + VGAP))
                    object_idx += 1
            object_rows = max(object_rows, object_idx)

        hana_width = max((hana_x[d] + col_width[d] for d in hana_cols), default=0)
        object_width = max((object_x[d] + col_width[d] for d in object_cols), default=0)
        max_rows = max(hana_rows, object_rows)
        width = max(hana_width, object_width) + band_pad_x * 2
        height = object_top + band_label_h + max(1, object_rows) * (H + VGAP) - VGAP + 18
    else:
        for d in cols:
            for r, n in enumerate(by_col[d]):
                pos[n["name"]] = (col_x[d], HEAD + r * (H + VGAP))
            max_rows = max(max_rows, len(by_col[d]))
        width = (acc - COLGAP) if cols else 0
        height = HEAD + max_rows * (H + VGAP) - VGAP + 4
    p = [f'<svg class="lineage-svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
         f'role="img" aria-label="Diagrama de trazabilidad del flujo">',
         '<defs><marker id="ln-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
         '<path d="M0,0 L8,4 L0,8 z" class="ln-arrowhead"/></marker></defs>']

    runtime_labels = {"hana": "HANA Space", "object_store": "HDLF Object Store", "data_product": "Data Product"}
    runtime_groups = defaultdict(list)
    for n in shown:
        runtime = str(n.get("runtime") or "").strip()
        if runtime:
            runtime_groups[runtime].append(n)
    for runtime, items in runtime_groups.items():
        xs = [pos[n["name"]][0] for n in items if n["name"] in pos]
        ys = [pos[n["name"]][1] for n in items if n["name"] in pos]
        if not xs or not ys:
            continue
        x0 = max(0, min(xs) - 12)
        y0 = max(0, min(ys) - 18)
        x1 = max(pos[n["name"]][0] + node_w[n["name"]] for n in items if n["name"] in pos) + 12
        y1 = max(pos[n["name"]][1] + H for n in items if n["name"] in pos) + 12
        p.append(f'<rect class="ln-runtime-box ln-runtime-box--{html.escape(runtime)}" x="{x0:.0f}" y="{y0:.0f}" width="{x1-x0:.0f}" height="{y1-y0:.0f}" rx="10"/>')
        p.append(f'<text class="ln-runtime-label" x="{x0+10:.0f}" y="{y0+12:.0f}">{html.escape(runtime_labels.get(runtime, runtime))}</text>')

    runtime_by_name = {n["name"]: str(n.get("runtime") or "") for n in shown}
    lineage_key_by_name = {n["name"]: slugify(str(n.get("label") or n.get("title") or n.get("name") or "")) for n in shown}
    lineage_key_counts = defaultdict(int)
    for _key in lineage_key_by_name.values():
        if _key:
            lineage_key_counts[_key] += 1
    duplicate_lineage_keys = {k for k, v in lineage_key_counts.items() if v > 1}
    if duplicate_lineage_keys:
        p.append("<style>" + "".join(
            f'.lineage-svg:has(.ln-k-{html.escape(k)}:hover) .ln-k-{html.escape(k)} .ln-node{{stroke:var(--gold);stroke-width:1.8;}}'
            for k in sorted(duplicate_lineage_keys)
        ) + "</style>")

    def _edge(up, tgt, cls):
        if up not in pos or tgt not in pos:
            return
        edge_cls = cls
        if is_data_product_lineage and runtime_by_name.get(up) == "hana" and runtime_by_name.get(tgt) == "object_store" and "ln-edge--assoc" not in cls:
            edge_cls = cls + " ln-edge--hana"
        x1, y1 = pos[up]
        x2, y2 = pos[tgt]
        sx, sy, tx, ty = x1 + node_w[up], y1 + H / 2, x2, y2 + H / 2
        dx = max(24, abs(tx - sx) / 2)
        p.append(f'<path class="{edge_cls}" d="M{sx:.0f},{sy:.0f} C{sx+dx:.0f},{sy:.0f} '
                 f'{tx-dx:.0f},{ty:.0f} {tx:.0f},{ty:.0f}" marker-end="url(#ln-arrow)"/>')

    for up, tgt in direct_edges:
        _edge(up, tgt, "ln-edge")
    if show_assoc:
        for up, tgt in assoc_edges:
            _edge(up, tgt, "ln-edge ln-edge--assoc")
    for n in shown:
        x, y = pos[n["name"]]
        w, short = node_w[n["name"]], node_disp[n["name"]]
        subtext = str(n.get("subtext") or "").strip()
        main_y = y + 18 if subtext else y + H / 2 + 4
        sub_html = (f'<text class="ln-node-subtext" x="{x+32}" y="{y+32:.0f}">{html.escape(subtext)}</text>' if subtext else '')
        lineage_key = lineage_key_by_name.get(n["name"], "")
        lineage_key_class = f" ln-k-{lineage_key}" if lineage_key in duplicate_lineage_keys else ""
        p.append(
            f'<a href="#{n["slug"]}" class="ln-node-a{lineage_key_class}"><title>{html.escape(n["title"])}</title>'
            f'<rect class="ln-node ln-layer-{n["layer"]}" x="{x}" y="{y}" width="{w}" height="{H}" rx="8"/>'
            f'<text class="ln-num" x="{x+11}" y="{main_y:.0f}">{n["num"]:02d}</text>'
            f'<text class="ln-node-text" x="{x+32}" y="{main_y:.0f}">{html.escape(short)}</text>{sub_html}</a>')
    p.append("</svg>")
    return '<div class="catalog-lineage__scroll">' + "".join(p) + "</div>"


def _catalog_apis_dir() -> Path:
    """Carpeta de código de las CDS views (extractores corporativos), compartida a
    nivel raíz del catálogo (una copia por CDS, agnóstica de cliente). El generador
    la descarga desde SharePoint (CSN/APIs) a catalog_local/APIs al regenerar."""
    from config import BASE_DIR
    root = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
    return Path(root) / "APIs"


def _cds_name_from_origen(origen: str) -> str:
    """`ABAP_CDS.ZCDS_DSP_DIM_MCH1$E` → `ZCDS_DSP_DIM_MCH1`. "" si el origen no es
    una CDS view corporativa (ABAPTABLES / BW Bridge / vacío → sin código que abrir)."""
    if not origen or not str(origen).upper().startswith("ABAP_CDS."):
        return ""
    return str(origen).split(".", 1)[1].split("$", 1)[0].strip()


def _catalog_cds_link(origen: str) -> str:
    """Nombre de la CDS view SOLO si su código existe en APIs/. "" si no hay origen
    ABAP_CDS o el archivo no está (así el botón no se renderiza y no hay link muerto
    ni error de consola). Es lo que hace el auto-vínculo robusto."""
    cds = _cds_name_from_origen(origen)
    if cds and (_catalog_apis_dir() / f"{cds}.md").exists():
        return cds
    return ""


def _catalog_cds_view(name: str):
    """Localiza APIs/<name>.md y lo prepara para renderizar con el MISMO pipeline de
    contenido (hero + secciones). Devuelve (meta, title, intro, sections) o None si
    no existe (el endpoint responde 404; el botón nunca se muestra sin archivo)."""
    from webapp.services.content_service import render_sections, split_front_matter
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    path = _catalog_apis_dir() / f"{name}.md"
    if not path.exists():
        return None
    meta, body = split_front_matter(path.read_text(encoding="utf-8", errors="ignore"))
    body = body.strip()
    if body.count("```") % 2:   # fence sin cerrar (olvido del autor) → cerrar, tolerante
        body += "\n```"
    mt = re.search(r"^#\s+(.+)$", body, re.M)
    title = mt.group(1).strip() if mt else name
    rest = body[mt.end():] if mt else body
    h2 = re.search(r"^##\s+", rest, re.M)
    intro = (rest[:h2.start()] if h2 else rest)
    intro = re.sub(r"^[-\s]*$", "", intro, flags=re.M).strip().strip("_").strip()
    sections_md = rest[h2.start():] if h2 else ""
    sections = render_sections(sections_md, pack_name="", stem="", base=0) if sections_md else ()
    return meta, title, intro, sections



def _catalog_compliance_status(raw: str) -> tuple[str, str]:
    """Map generated compliance markers/text to a visual status class + label."""
    value = (raw or "").lower()
    if any(token in value for token in ("rojo", "crit", "grave", "bloque", "no cumple", "??")):
        return "red", "Critico"
    if any(token in value for token in ("naranja", "parcial", "riesgo", "atender", "??")):
        return "orange", "Atender"
    if any(token in value for token in ("amarillo", "advert", "warning", "observ", "buena", "reutilizable con notas", "??")):
        return "amber", "Observar"
    if any(token in value for token in ("verde", "cumple", "plena", "correcto", "?", "?", "??")):
        return "green", "Cumple"
    return "blue", "Nota"


def _catalog_inline_html(text: str) -> str:
    safe = html.escape((text or "").strip())
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<em>\1</em>", safe)
    return safe


def _catalog_evidence_html(text: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return '<span class="catalog-compliance__muted">Sin evidencia detallada.</span>'
    parts = [part.strip() for part in clean.split(" | ") if part.strip()]
    if len(parts) >= 2:
        items = "".join(f"<li>{_catalog_inline_html(part)}</li>" for part in parts)
        return f'<ul class="catalog-compliance__evidence-list">{items}</ul>'
    comma_parts = [part.strip() for part in clean.split(",") if part.strip()]
    if len(comma_parts) >= 3:
        artifact_like = sum(
            1
            for part in comma_parts
            if "->" in part
            or "→" in part
            or "ABAP_CDS." in part.upper()
            or re.search(r"\bZ[A-Z0-9_*$]+\b", part.upper())
        )
        if artifact_like >= max(2, len(comma_parts) // 2):
            items = "".join(f"<li>{_catalog_inline_html(part)}</li>" for part in comma_parts)
            return f'<ul class="catalog-compliance__evidence-list">{items}</ul>'
    return _catalog_inline_html(clean)


def _catalog_compliance_table_html(source: str) -> str:
    """Render generated catalog compliance bullets as a readable semaphore table.

    The generator keeps producing simple markdown. This presentation layer only
    upgrades the final `Hallazgos de cumplimiento` block when the pattern is present.
    """
    block = (source or "").strip()
    if not block:
        return ""
    item_re = re.compile(
        r"(?ms)^-\s*(?P<marker>\S+)?\s*\*\*(?P<title>.+?)\*\*\s*(?P<body>.*?)(?=\n-\s+\S*\s*\*\*|\Z)"
    )
    rows = []
    for match in item_re.finditer(block):
        marker = match.group("marker") or ""
        title = re.sub(r"\s+", " ", match.group("title") or "").strip()
        body = match.group("body") or ""
        ev_match = re.search(r"(?is)_?Evidencia:_?\s*(.*)$", body)
        evidence = ev_match.group(1).strip() if ev_match else body.strip()
        status_class, status_label = _catalog_compliance_status(f"{marker} {title} {evidence}")
        rows.append(
            "<tr>"
            f'<td class="catalog-compliance__state"><span class="catalog-status catalog-status--{status_class}">{status_label}</span></td>'
            f'<td class="catalog-compliance__finding">{_catalog_inline_html(title)}</td>'
            f'<td class="catalog-compliance__evidence">{_catalog_evidence_html(evidence)}</td>'
            "</tr>"
        )
    if not rows:
        return block
    return (
        '<div class="catalog-compliance">'
        '<table class="catalog-compliance-table">'
        '<thead><tr><th>Estado</th><th>Hallazgo</th><th>Evidencia</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</div>'
    )

def _catalog_clean_label(raw, fallback: str) -> str:
    """Runtime twin de `_clean_label` del generador: descarta un label que en realidad
    es un placeholder i18n sin resolver (p. ej. '{i18n>Remote.X@ENDUSERTEXT.LABEL}')
    y cae al fallback (nombre técnico). Cubre .md viejos sin regenerar."""
    s = str(raw or "").strip()
    if not s:
        return fallback
    low = s.lower()
    if ("{i18n" in low or "@enduserext.label" in low
            or (s.startswith("{") and s.endswith("}") and "@" in s)):
        return fallback
    return s


def _catalog_flow_view(entry, show_assoc: bool = False, pack: str = ""):
    """Arma el detalle de un flujo: (a) un diagrama SVG de linaje por capas y (b)
    las secciones estilo-módulo (una por artefacto, con sus tablas), renderizadas
    con el MISMO pipeline que los módulos (content_service.render_sections) para
    heredar el look & feel — no replicamos la lógica visual. El índice de secciones
    ES el linaje textual; el diagrama lo hace visual. El título de sección es la
    DESCRIPCIÓN con el nombre técnico entre llaves — `Descripción {NOMBRE}` — o solo
    el nombre si no hay descripción. Cierra con una sección de cumplimiento (los
    hallazgos del flujo_*.md). Las referencias entre artefactos y los nodos del
    diagrama linkean al ancla de la sección (mismo slug que el título mostrado).
    Devuelve (sections, lineage_svg)."""
    from webapp.services.content_service import render_sections, split_front_matter
    from core.utils import slugify

    def _heading(name: str, label: str) -> tuple[str, str]:
        """(heading con tonos para la sección, texto plano para el slug). Formato
        consistente en dos partes `A · B` (A en negro, B en dorado): con descripción
        → `{dw-black}Descripción · {dw-gold}NOMBRE`; sin descripción → se repite el
        nombre técnico `{dw-black}NOMBRE · {dw-gold}NOMBRE` (misma estructura visual)."""
        desc = label if (label and label != name) else name
        return f"{{dw-black}}{desc} · {{dw-gold}}{name}", f"{desc} · {name}"

    vistas_dir = Path(entry.path).parent / "vistas"
    names = list(entry.artefactos or [])
    bodies, headings, plains, autos = {}, {}, {}, {}
    for name in names:
        vpath = vistas_dir / f"{name}.md"
        if not vpath.exists():
            continue
        meta, body = split_front_matter(vpath.read_text(encoding="utf-8"))
        auto = meta.get("auto") or {}
        label = _catalog_clean_label(auto.get("label"), name)
        bodies[name] = body.strip()
        headings[name], plains[name] = _heading(name, label)
        autos[name] = auto
    slug_of = {name: slugify(plains[name]) for name in bodies}   # ancla = slug del texto plano (sin tonos)

    # Reclasificación directo/asociación (idempotente con el generador nuevo): una
    # entidad analítica (hecho/dimensión/cubo) tiene como linaje DIRECTO solo su base
    # transformacional (vista relacional / hecho); dimensiones, textos y fuentes
    # crudas → asociación (como Datasphere). Se aplica en runtime para que valga sin
    # regenerar los .md viejos.
    def _is_transform_base(x: str) -> bool:
        a = autos.get(x)
        if not a:
            return False
        sem = f"{a.get('data_category') or ''} {a.get('modeling_pattern') or ''}".upper()
        return (not a.get("origen_remoto")) and ("DIMENSION" not in sem)

    def _split_up(auto: dict) -> tuple[list, list]:
        up = [u for u in (auto.get("upstreams") or []) if u in bodies]
        assoc = [u for u in (auto.get("upstreams_asociacion") or []) if u in bodies]
        sem = f"{auto.get('data_category') or ''} {auto.get('modeling_pattern') or ''}".upper()
        if any(k in sem for k in ("FACT", "DIMENSION", "CUBE")):
            demote = {x for x in up if not _is_transform_base(x)}   # solo degradar, nunca promover
            up = [x for x in up if x not in demote]
            assoc = sorted(set(assoc) | demote)
        return up, assoc

    # Nodos del flujo (sin num todavía: se numera por posición en el ORDEN VISIBLE).
    nodes, order = [], [n for n in names if n in bodies]
    for name in order:
        auto = autos[name]
        label = _catalog_clean_label(auto.get("label"), name)
        up, assoc = _split_up(auto)
        sem = f"{auto.get('data_category') or ''} {auto.get('modeling_pattern') or ''}".upper()
        nodes.append({
            "name": name, "num": 0, "slug": slug_of[name],
            "title": label if label != name else name,   # tooltip = descripción (o nombre si no hay)
            "label": name,                                # nodo = nombre técnico
            "layer": _catalog_layer_index(auto, name),
            "is_factcube": ("FACT" in sem or "CUBE" in sem),
            "upstreams": up,
            "assoc_upstreams": assoc,
        })

    # keep = artefactos visibles según el toggle. Las secciones del detalle y el SVG
    # se filtran y NUMERAN por el mismo orden visible (shown_order) → los números del
    # nodo y de su sección coinciden y se adaptan dinámicamente al toggle.
    keep = _catalog_flow_keep(nodes, show_assoc)
    shown_order = [name for name in order if name in keep] or order
    num_of = {name: i + 1 for i, name in enumerate(shown_order)}
    for n in nodes:
        n["num"] = num_of.get(n["name"], 0)
    lineage_svg = _catalog_lineage_svg(nodes, show_assoc, keep)
    parts = []
    for name in shown_order:
        # H1 → H2 (lambda: la descripción con tonos puede traer caracteres que re
        # interpretaría como grupos de reemplazo).
        vbody = re.sub(r"^\s*#\s+.*$", lambda _m: f"## {headings[name]}", bodies[name], count=1, flags=re.M)
        # Botón "Abrir CDS View": auto-vínculo al código del extractor cuando el
        # origen es ABAP_CDS.<X> y existe APIs/<X>.md (si no, no se renderiza →
        # robusto, sin link muerto). Va como bloque HTML tras el heading (markdown
        # lo levanta al header por venir marcado como heading-supports). Al volver del código se re-abre este mismo flujo.
        cds = _catalog_cds_link(autos[name].get("origen_remoto") or "")
        if cds:
            qs = urlencode({"name": cds, "cliente": entry.cliente,
                            "dominio": entry.dominio, "pack": pack or DEFAULT_PACK})
            btn = (f'\n\n<div class="support-links heading-supports catalog-cds-launch">'
                   f'<a class="support-link catalog-cds-link" href="#" role="button" '
                   f'hx-get="/catalog/cds?{qs}" hx-target="#catalog-detail-slot" hx-swap="innerHTML" '
                   f'@click.prevent>Abrir CDS View <span>&#8249;/&#8250;</span></a></div>\n\n')
            vbody = re.sub(r"(?m)^##\s+.*$", lambda m: m.group(0) + btn, vbody, count=1)
        parts.append(vbody)
    # Sección de cumplimiento: reutiliza el bloque "## Hallazgos…" del flujo_*.md.
    mh = re.search(r"##\s*Hallazgos[^\n]*\n(.*)$", entry.body or "", re.S)
    if mh:
        verdict = str(entry.veredicto or "").strip().lower()
        intro = _catalog_compliance_intro_md(
            verdict,
            str(entry.apto or _catalog_verdict_text(verdict)),
            f"el dominio {entry.dominio} de {entry.cliente}",
        )
        parts.append("## Cumplimiento\n\n" + intro + "\n\n" + _catalog_compliance_table_html(mh.group(1)))
    composed = "\n\n".join(parts)
    # Linkear en las tablas → ancla de su sección, solo para artefactos VISIBLES
    # (evita links muertos a secciones filtradas por el toggle).
    shown_set = set(shown_order)
    composed = re.sub(
        r"`([A-Za-z0-9_]+)`",
        lambda m: (f"[{m.group(1)}](#{slug_of[m.group(1)]})" if m.group(1) in shown_set else m.group(0)),
        composed,
    )
    return render_sections(composed, pack_name="", stem="", base=0), lineage_svg


@app.get("/catalog/flow", response_class=HTMLResponse, include_in_schema=False)
def catalog_flow(request: Request, cliente: str = "", dominio: str = "",
                 pack: str = Query(DEFAULT_PACK), assoc: int = 0, frag: str = ""):
    """Detalle de un flujo del catálogo, renderizado en el panel central con el
    MISMO pipeline de contenido de los módulos (hero + diagrama de linaje +
    artículo .methodology). El contenido vive en los .md del catálogo. `assoc=1`
    incluye las aristas por asociación (indirectas) en el diagrama; `frag=lineage`
    devuelve solo el bloque del diagrama (para el toggle vía HTMX)."""
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_entries()
    entry = mod.find_flow(entries, cliente, dominio) if mod else None
    if entry is None:
        raise HTTPException(404, "Flujo no encontrado en el catálogo")
    show_assoc = bool(assoc)
    sections, lineage_svg = _catalog_flow_view(entry, show_assoc, selected)
    lineage_ctx = {"lineage_svg": lineage_svg, "show_assoc": show_assoc,
                   "cliente": entry.cliente, "dominio": entry.dominio, "pack": selected}
    if frag == "lineage":   # solo el bloque del diagrama (toggle Asociaciones)
        return templates.TemplateResponse(request=request, name="partials/catalog_lineage.html",
                                          context=lineage_ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    # HTMX → solo el fragmento (actualiza #content sin salir del módulo ni perder
    # el panel derecho). Navegación directa → página completa con shell + nav.
    tmpl = "partials/catalog_flow_content.html" if request.headers.get("HX-Request") else "catalog_flow.html"
    return templates.TemplateResponse(
        request=request, name=tmpl,
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request), "pack": selected,
            "pack_label": content_pack_short_label(selected), "user": user,
            "cliente": entry.cliente, "dominio": entry.dominio, "icon": entry.icon,
            "veredicto": entry.veredicto, "apto": entry.apto,
            "n_art": len(entry.artefactos), "sections": sections,
            "catalog_kind": "domain",
            "catalog_ref": f"{entry.cliente} · {entry.dominio}",
            **lineage_ctx,
        },
    )


@app.get("/catalog/data-product", response_class=HTMLResponse, include_in_schema=False)
def catalog_data_product(request: Request, product_id: str = "", pack: str = Query(DEFAULT_PACK), lineage: int = 0):
    """Detalle central de un Data Product del catálogo BDC."""
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    mod, entries = _catalog_data_products_entries()
    entry = mod.find_data_product(entries, product_id) if mod else None
    if entry is None:
        raise HTTPException(404, "Data Product no encontrado en el catálogo")
    from webapp.services.content_service import render_sections
    sections = render_sections(entry.body, pack_name="", stem="", base=0)
    show_lineage = bool(lineage)
    lineage_svg = _catalog_data_product_lineage_svg(entry, show_lineage)
    ctx = {
        "product": entry,
        "sections": sections,
        "lineage_svg": lineage_svg,
        "show_lineage": show_lineage,
        "pack": selected,
        "catalog_kind": "data_product",
        "catalog_ref": entry.product_id,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/catalog_data_product_content.html", context=ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_data_product.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request),
            "pack_label": content_pack_short_label(selected), "user": user,
            **ctx,
        },
    )


@app.get("/catalog/cds", response_class=HTMLResponse, include_in_schema=False)
def catalog_cds(request: Request, name: str = "", cliente: str = "", dominio: str = "",
                pack: str = Query(DEFAULT_PACK)):
    """Código de una CDS view (extractor corporativo), renderizado en el panel
    central con el mismo pipeline de contenido. Se abre desde el botón "Abrir CDS
    View" del detalle de un flujo; "Volver" re-abre ese flujo (cliente+dominio)."""
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    view = _catalog_cds_view(name)
    if view is None:
        raise HTTPException(404, "CDS view no encontrada en el catálogo")
    meta, title, intro, sections = view
    ctx = {
        "cds_name": name, "cds_title": title, "cds_intro": intro,
        "tabla_base": str(meta.get("tabla_base") or ""),
        "extraccion": str(meta.get("extraccion") or ""),
        "sections": sections, "cliente": cliente, "dominio": dominio, "pack": selected,
        "catalog_kind": "cds",
        "catalog_ref": name,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request,
                                          name="partials/catalog_cds_content.html", context=ctx)
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_cds.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            "active_group": "", "active_stem": "",
            "readonly": _readonly(request),
            "pack_label": content_pack_short_label(selected), "user": user,
            **ctx,
        },
    )


@app.get("/catalog/guide/{slug}", response_class=HTMLResponse, include_in_schema=False)
def catalog_guide(request: Request, slug: str, pack: str = Query(DEFAULT_PACK),
                  embed: int = 0, theme: str = ""):
    """Guía (autoservicio) para publicar un catálogo. GENÉRICA por `slug`: cada
    how-to es un `guides/<slug>.yaml` del pack (dominios, orígenes/conexiones, …) —
    agregar una guía nueva NO requiere código, solo el YAML + un link. Se cuenta como
    storytelling estilo Danwor Academy (slides + rail): `embed=1` devuelve las slides
    (contenido del iframe); sin embed, el wrapper con shell. El CONTENIDO vive en el
    pack; lo carga guides_service. Capturas desde assets/<assets_dir>/ del pack."""
    user = auth_service.session_user(request)
    if not user:
        return RedirectResponse(f"/?pack={_pack(pack)}#acceso", status_code=307)
    selected = _pack(pack) if _diagnostic(request) else DEFAULT_PACK
    guide = guides.load(selected, slug)
    if guide is None:
        raise HTTPException(404, "Guía no encontrada en el pack")

    theme_cookie = request.cookies.get("dw-content-theme", "light")
    resolved_theme = (theme or theme_cookie)
    if resolved_theme not in ("light", "dark"):
        resolved_theme = "light"

    # Destino del "← Volver" (dentro del contenido, como en los detalles de flujo):
    # el módulo/pestaña Catálogo desde donde se invoca la guía. Lo decide el CONTENIDO
    # (guide.back_module: dominios→04, orígenes→01); default 04 si no viene.
    back_num = (guide.get("back_module") or "04").zfill(2)
    back_mod = next((m for m in list_modules(selected)
                     if str(m.number).zfill(2) == back_num
                     or m.stem.startswith(f"{back_num}_")), None)
    back_url = (f"/modules/{back_mod.stem}?pack={selected}&tab=catalogo" if back_mod
                else f"/?pack={selected}")

    # embed=1 → contenido de las slides (lo monta el iframe del wrapper).
    if embed:
        return templates.TemplateResponse(
            request=request, name="catalog_guide_slides.html",
            context={"pack": selected, "guide": guide, "steps": guide["steps"],
                     "theme": resolved_theme, "back_url": back_url, "slug": slug},
        )

    # Wrapper (navegación directa desde el link "¿Falta …?"): shell + academy-stage
    # con las slides embebidas (mismo patrón que /academy/{stem}). Sin panel derecho.
    iframe_src = (
        f"/catalog/guide/{quote(slug, safe='')}?"
        f"{urlencode({'embed': '1', 'pack': selected, 'theme': resolved_theme, 'branding': 'danwor'})}"
    )
    # Topbar completo, igual que módulos/Academy: el selector de marco (diagnóstico)
    # y el toggle de diagnóstico se muestran según el mismo criterio, para que el
    # alcance de botones sea el mismo que en la vista desde la que se invocó.
    diagnostic = _diagnostic(request)
    packs = [
        {"name": name, "label": content_pack_short_label(name),
         "description": content_pack_label(name), "home": _home_url(name)}
        for name in available_content_packs()
    ]
    academy = [a for a in list_academy(selected) if not a.nav_hidden]
    return templates.TemplateResponse(
        request=request, name="catalog_guide.html",
        context={
            "modules": list_modules(selected),
            "appendices": [a for a in list_appendices(selected) if not a.nav_hidden],
            "academy": academy,
            "academy_assets": {a.stem: academy_asset_for(a.stem, selected) for a in academy},
            # Nav izquierdo con el grupo CORE expandido: la guía no es un módulo
            # (active_stem=""), pero así la lista de módulos queda visible para
            # continuar de un click (además del "← Volver" al módulo que la llamó).
            "active_group": "core", "active_stem": "",
            "diagnostic": diagnostic, "is_owner": _is_owner(request), "packs": packs,
            "readonly": _readonly(request), "pack": selected,
            "pack_label": content_pack_short_label(selected), "user": user,
            "iframe_src": iframe_src, "show_panel": False,
        },
    )


@app.get("/catalog/como-publicar", response_class=HTMLResponse, include_in_schema=False)
def catalog_guide_legacy(request: Request, pack: str = Query(DEFAULT_PACK)):
    """Alias histórico: la ruta se generalizó a /catalog/guide/{slug}. Redirige a la
    guía de dominios para no romper links viejos."""
    return RedirectResponse(
        f"/catalog/guide/como-publicar-dominios?pack={_pack(pack)}", status_code=307)


@app.post("/catalog/regenerate", include_in_schema=False)
def catalog_regenerate(request: Request, graph_token: str = Form("")):
    """Self-service: regenera el catálogo desde la guía (botón del paso 4), sin
    pasar por el panel de gestión (owner-only). Gate = estar logueado; la lectura
    de SharePoint usa el token DELEGADO del propio colaborador (Graph), así que la
    frontera de seguridad es SharePoint (solo ve el CSN al que ya tiene acceso).
    Serializado por lock (no corre dos veces en paralelo) y auditado."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    ran, message = admin_tools.run_catalog_generation_locked(graph_token=graph_token)
    ok = ran
    # Token de Graph vencido → SharePoint responde 401. Lo traducimos a una acción
    # clara para el colaborador (el token no se puede refrescar sin re-login).
    if ran and "401" in message and "unauthorized" in message.lower():
        ok = False
        message = ("Tu token SharePoint expiró, necesitamos que reinicies tu sesión al "
                   "copiloto (logout/login) y vuelvas a repetir esta acción con un token nuevo.")
    log_activity("catalog_regenerate", user=user, ran=ran, ok=ok)
    return JSONResponse({"ok": ok, "message": message})


@app.post("/admin/catalog/generate", response_class=HTMLResponse)
def admin_catalog_generate(request: Request, graph_token: str = Form("")):
    _require_diagnostic(request)
    output = admin_tools.run_catalog_generation(graph_token=graph_token)
    return _admin_result(request, "Catálogo CSN — regeneración", output)


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
