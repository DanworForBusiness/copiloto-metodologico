"""
access.py — Access policy de la web actual (request-scoped).

Concentra las DECISIONES DE ACCESO de la aplicación web: quién es el dueño
(APP_OWNER), quién es perfil de solo-lectura (tier academy del Copiloto) y quién
puede activar el modo diagnóstico. Se extrajo de ``webapp/main.py`` (gate
distribuido pre-2D) para que los routers con templates puedan consultarlas sin
importar ``webapp.main``.

Distinción deliberada respecto de ``auth_service``: ``auth_service`` es capacidad
PLATFORM / SHARED (identidad, sesión, tokens, tiers). Lo de acá son POLÍTICAS de
la web ACTUAL; en particular ``is_readonly == tier academy`` es semántica actual
del Copiloto y NO debe contaminar ``auth_service``. NO se asume todavía que estas
políticas sean universales para futuros workspaces de la plataforma.

NO contiene: verificación de tokens, creación/borrado de sesión, Graph, content
packs, templates, ``RedirectResponse``, route handlers ni ``HTTPException`` de
admin. NO importa ``webapp.main``.

Seam de tests: ``APP_OWNER`` se enlaza acá desde config y las funciones leen el
global del módulo, así que los tests reasignan ``webapp.access.APP_OWNER`` (mismo
patrón que ``auth_service.ALLOWED_USERS``/``ACADEMY_USERS``).

CONTRATO DE PRODUCTO (owner decision, corrective post-#107): `APP_OWNER` pasa a
ser superusuario de TODOS los workspaces registrados del Hub (Copiloto/
Operations/Commercial/Capital Humano), no sólo del diagnóstico del Copiloto y
del fallback histórico de Hub Metrics. Esto REEMPLAZA la política anterior
(documentada hasta este corrective en `human_capital_access.py`/
`operations_access.py`/`commercial_access.py`: "APP_OWNER NUNCA se referencia
acá, sin bypass implícito, el dueño debe estar explícito en cada allowlist como
cualquier otro usuario") — decisión consciente del owner, no un bug. `is_owner_user`
(abajo) es el ÚNICO lugar que implementa la excepción; cada predicado de
workspace la consume vía `or`, nunca la reimplementa. CRÍTICO: esto SOLO
afecta AUTORIZACIÓN post-autenticación — `APP_OWNER` nunca crea ni valida una
sesión por sí solo; un `APP_OWNER` sin sesión válida sigue sin acceso a nada
(ver `is_owner_user`, que exige `user` no-None primero).
"""
from __future__ import annotations

from fastapi import Request

from webapp.services import auth_service
from config import APP_OWNER, AUTH_DEFAULT_DOMAIN, DIAGNOSTIC_MODE


def owner_emails() -> set[str]:
    """APP_OWNER → set de mails. Acepta CSV y alias sin dominio (joconnor →
    joconnor@danwor.com), igual que ALLOWED_USERS."""
    domain = (AUTH_DEFAULT_DOMAIN or "danwor.com").strip().lower().lstrip("@")
    owners = set()
    for raw in (APP_OWNER or "").split(","):
        u = raw.strip().lower()
        if u:
            owners.add(u if "@" in u else f"{u}@{domain}")
    return owners


def is_owner_user(user: dict | None) -> bool:
    """Predicado PURO (sin `Request`) — True si `user` (claims de sesión, o
    None) es el dueño de la app (config APP_OWNER). ÚNICO lugar que decide
    esto; `is_owner(request)` (abajo) y los predicados de autorización de
    Operations/Commercial/Capital Humano delegan acá, nunca reimplementan la
    comparación contra `owner_emails()`. Exige `user` no-None PRIMERO —
    `APP_OWNER` nunca es un atajo de autenticación, sólo de autorización
    posterior a una sesión ya válida."""
    if not user:
        return False
    owners = owner_emails()
    if not owners:
        return False
    return str(user.get("sub") or "").strip().lower() in owners


def is_owner(request: Request) -> bool:
    """True si el usuario logueado es el dueño de la app (config APP_OWNER).
    Wrapper HTTP sin cambio de comportamiento — delega en el predicado puro
    `is_owner_user`, MISMO criterio pure/wrapper que el resto de los
    predicados de workspace (`human_capital_access`/`operations_access`/
    `commercial_access`)."""
    return is_owner_user(auth_service.session_user(request))


def is_readonly(request: Request) -> bool:
    """True si el usuario logueado es perfil SOLO LECTURA (ACADEMY_USERS): navega y
    lee todo el contenido, pero con la IA apagada (sin asistente ni análisis de
    sección → sin consumo de tokens). Se deriva del email en cada request, así que
    mover un usuario entre listas surte efecto sin necesidad de re-login."""
    user = auth_service.session_user(request)
    if not user:
        return False
    return auth_service.user_tier(str(user.get("sub") or "")) == "academy"


def is_diagnostic(request: Request) -> bool:
    """Modo diagnóstico: solo el dueño (APP_OWNER) puede activarlo por cookie.

    Habilita el selector de marco metodológico y la pestaña/grupo de gestión.
    DANWOR_DIAGNOSTIC_MODE lo fuerza por entorno (dev). Para usuarios no-owner
    queda siempre off, aunque tengan la cookie seteada a mano.
    """
    if DIAGNOSTIC_MODE:
        return True
    return is_owner(request) and request.cookies.get("dw-diagnostic") == "on"
