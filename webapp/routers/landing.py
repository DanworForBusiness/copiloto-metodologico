"""
landing.py — Landing / session navigation (feature COPILOT).

Router de las 6 rutas de entrada/navegación de sesión: la raíz (`GET /`), su
alias explícito (`GET /copilot`), el destino post-login dependiente de
perfil (`GET /home`), el atajo a la sección de acceso (`GET /login`) y la
invitación one-off al Desafío (`GET /desafio`, `GET /desafio/cerrar`).
Extraído de ``webapp/main.py`` (Fase 2G) como relocación pura — sin
rediseño de auth/pack/navegación/invite.

`/copilot` (batch de transición multi-workspace) es un alias EXPLÍCITO de
`/`, no un segundo producto: comparte la misma `render_landing()` — mismo
auth, misma sesión, mismo mecanismo de `next`, mismo template, misma copy —
así que no puede divergir de `/` por construcción. `/` sigue siendo la
puerta del Copiloto sin cambios (decisión de producto: NO se flipea a
Platform en este batch). La puerta "One Danwor" (`/onedanwor`, selección de
workspace; `/platform` como alias de compatibilidad que redirige ahí) vive
en `webapp/routers/platform.py` y reutiliza `render_landing()` con una copy
distinta — sin duplicar la lógica acá.

Feature COPILOT que consume capacidades compartidas desde sus dueños explícitos
(`core.content_registry`, `webapp.services.auth_service`, `webapp.access`,
`webapp.nav`, `webapp.services.content_service`, `webapp.templating`,
`webapp.presentation.invite`, `config`) SIN importar ``webapp.main``.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import DESAFIO_CLOSE_URL, SESSION_COOKIE_SAMESITE, SESSION_COOKIE_SECURE
from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp import access
from webapp.nav import home_url, login_redirect
from webapp.presentation.invite import INVITE_COOKIE
from webapp.services import auth_service
from webapp.services.content_service import list_academy
from webapp.templating import templates

router = APIRouter()

# Copy EXACTA del texto hardcodeado histórico de landing.html — extraída acá
# para que `/` y `/copilot` sigan renderizando byte a byte lo mismo de
# siempre. `webapp/routers/platform.py` define su propia copy (distinta,
# platform-neutral) sin tocar esta.
_COPILOT_LANDING_COPY = {
    "title": "Copiloto Metodológico · Danwor",
    "eyebrow_ink": "Copiloto Metodológico",
    "eyebrow_gold": "Arquitecto Danwor",
    "brand_title": "Copiloto Metodológico",
    "brand_subtitle": "Arquitecto Danwor",
    "brand_description": (
        "Un entorno guiado para recorrer nuestro marco metodológico, consultar "
        "decisiones técnicas y acelerar entregables con criterio arquitectónico "
        "y mirada Danwor."
    ),
    "feature_list": [
        "Marco metodológico navegable",
        "Árboles de decisión y asistencia IA",
        "Criterios técnicos para proyectos SAP",
    ],
    "login_sub_lines": [
        "Copiloto Metodológico · Arquitecto Danwor",
        "SAP Datasphere · BDC-Ready · Inteligencia metodológica",
    ],
}


def render_landing(request: Request, selected_pack: str, next: str, *, default_home: str, copy: dict):
    """Comportamiento COMPLETO de la landing histórica (auth + redirect +
    template), parametrizado por `default_home`/`copy` para que `/`,
    `/copilot` y (desde `webapp/routers/platform.py`) `/onedanwor` sin
    sesión compartan exactamente la misma lógica — nunca duplicada."""
    # `next` viaja desde login_redirect cuando el visitante venía de un link directo
    # (p. ej. Academy) sin sesión: solo se acepta un path propio, relativo, para
    # evitar un open-redirect a un host externo.
    safe_next = next if next.startswith("/") and not next.startswith("//") else ""
    if auth_service.session_user(request):
        return RedirectResponse(safe_next or default_home, status_code=307)
    home_url = safe_next or default_home
    # `auth_popup`/`code`: esta misma landing es también la página que el POPUP
    # de Microsoft abre en ambos extremos del flujo (auth.js abre `<page
    # actual>?auth_popup=1`; Microsoft redirige de vuelta a `/?code=...`, fijo
    # por el redirect_uri registrado en Azure — no se puede variar por página).
    # Cubrir la landing con un overlay NO alcanza: el marcado con la marca de
    # ESTA página (Copilot, One Danwor, lo que sea) seguía existiendo debajo
    # del overlay, presente en el DOM. En vez de eso, se sirve un documento
    # SEPARADO y neutro (`auth_callback.html`) que nunca incluye ese copy —
    # no un segundo mecanismo de auth, solo un documento de presentación
    # distinto para ese estado técnico.
    if request.query_params.get("auth_popup") or request.query_params.get("code"):
        return templates.TemplateResponse(
            request=request, name="auth_callback.html", context={"home_url": home_url},
        )
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"pack": selected_pack, "home_url": home_url, "login_copy": copy},
    )


@router.get("/home", include_in_schema=False)
def home(request: Request, pack: str = Query(DEFAULT_PACK)):
    """Destino post-login, dependiente del perfil. Los usuarios de solo lectura
    (ACADEMY_USERS) aterrizan por defecto en Danwor Academy —su puerta de entrada—;
    los demás, en el primer módulo del marco. El contenido no se restringe: es solo
    el punto de aterrizaje inicial."""
    selected = resolve_pack(pack) if access.is_diagnostic(request) else DEFAULT_PACK
    if not auth_service.session_user(request):
        return login_redirect(request, selected)
    if access.is_readonly(request):
        academy = [item for item in list_academy(selected) if not item.nav_hidden]
        if academy:
            return RedirectResponse(f"/academy/{academy[0].stem}?pack={selected}", status_code=307)
    return RedirectResponse(home_url(selected), status_code=307)


# ── Invitación al Desafío (one-off) ──────────────────────────────────────
# URL directa (enviada por mail) que muestra la página del Desafío como dialog
# sobre el copiloto. No es navegable: el trigger es una cookie que sobrevive al
# login (el flujo post-login siempre aterriza en /home) y se borra al cerrar el
# dialog. Reusable: cada visita a /desafio la vuelve a armar.
@router.get("/desafio", include_in_schema=False)
def desafio_invite(request: Request, pack: str = Query(DEFAULT_PACK)):
    selected = resolve_pack(pack) if access.is_diagnostic(request) else DEFAULT_PACK
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


@router.get("/desafio/cerrar", include_in_schema=False)
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


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(request: Request, pack: str = Query(DEFAULT_PACK), next: str = Query("")):
    # Con sesión vigente, la raíz sigue de largo al copiloto: el wrapper de
    # danwor.com (/copiloto/) embebe esta raíz, y un usuario ya logueado debe
    # aterrizar adentro de la app, no en el landing con la sección de acceso.
    # data-home apunta a /home por defecto (redirección server-side según perfil), o
    # al `next` original si el visitante venía de un link directo sin sesión.
    selected = resolve_pack(pack)
    return render_landing(
        request, selected, next, default_home=f"/home?pack={selected}", copy=_COPILOT_LANDING_COPY,
    )


@router.get("/copilot", response_class=HTMLResponse, include_in_schema=False)
def copilot_entry(request: Request, pack: str = Query(DEFAULT_PACK), next: str = Query("")):
    """Alias explícito de `/` para el batch de transición multi-workspace —
    misma auth/sesión/next/template/copy, cero comportamiento nuevo."""
    selected = resolve_pack(pack)
    return render_landing(
        request, selected, next, default_home=f"/home?pack={selected}", copy=_COPILOT_LANDING_COPY,
    )


@router.get("/login", include_in_schema=False)
def login(pack: str = Query(DEFAULT_PACK)):
    # El acceso vive en la misma página que el landing (misma base, como el sitio
    # de marca). /login baja directo a la sección de acceso.
    return RedirectResponse(f"/?pack={resolve_pack(pack)}#acceso", status_code=307)
