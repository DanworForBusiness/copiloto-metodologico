"""
landing.py — Landing / session navigation (feature COPILOT).

Router de las 5 rutas de entrada/navegación de sesión: la raíz (`GET /`), el
destino post-login dependiente de perfil (`GET /home`), el atajo a la sección
de acceso (`GET /login`) y la invitación one-off al Desafío (`GET /desafio`,
`GET /desafio/cerrar`). Extraído de ``webapp/main.py`` (Fase 2G) como
relocación pura — sin rediseño de auth/pack/navegación/invite. NO implementa
el futuro modelo de workspace switcher / multi-workspace entry (ver HANDOFF
A.19) — Fase 2G solo conserva la navegación actual.

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
    selected = resolve_pack(pack)
    # `next` viaja desde login_redirect cuando el visitante venía de un link directo
    # (p. ej. Academy) sin sesión: solo se acepta un path propio, relativo, para
    # evitar un open-redirect a un host externo.
    safe_next = next if next.startswith("/") and not next.startswith("//") else ""
    default_home = f"/home?pack={selected}"
    # Con sesión vigente, la raíz sigue de largo al copiloto: el wrapper de
    # danwor.com (/copiloto/) embebe esta raíz, y un usuario ya logueado debe
    # aterrizar adentro de la app, no en el landing con la sección de acceso.
    if auth_service.session_user(request):
        return RedirectResponse(safe_next or default_home, status_code=307)
    # data-home apunta a /home por defecto (redirección server-side según perfil), o
    # al `next` original si el visitante venía de un link directo sin sesión.
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"pack": selected, "home_url": safe_next or default_home},
    )


@router.get("/login", include_in_schema=False)
def login(pack: str = Query(DEFAULT_PACK)):
    # El acceso vive en la misma página que el landing (misma base, como el sitio
    # de marca). /login baja directo a la sección de acceso.
    return RedirectResponse(f"/?pack={resolve_pack(pack)}#acceso", status_code=307)
