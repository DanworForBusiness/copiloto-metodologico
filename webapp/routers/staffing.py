"""
staffing.py — Operations / Staffing HTTP.

Rutas canónicas (batch Shared Workspace Shell + Operaciones Structure):
`GET /operaciones/staffing/asignaciones` (shell autenticado, sin Graph) y
`POST /operaciones/staffing/asignaciones/data` (recibe `graph_token`
explícito del browser, delega en `StaffingService`, devuelve un
fragmento HTML para HTMX). El nombre refleja la jerarquía real: workspace
Operaciones → área Staffing → capacidad Asignaciones.

`GET /operations/staffing` y `POST /operations/staffing/data` (nombres
históricos en inglés, de Batch 3 de aceleración) quedan como alias de
COMPATIBILIDAD — SIEMPRE 307 a la ruta canónica correspondiente, method y
body preservados (307, no 302/303). Nunca vuelven a implementar la lógica
de la página/fragmento — un solo punto de verdad evita que ambos
diverjan.

El token Graph delegado sigue viviendo en `sessionStorage` del browser
(mismo mecanismo de `webapp/static/auth.js` que ya usa el panel de Admin —
`hx-vals='js:{graph_token: sessionStorage.getItem("dw-graph-token") ...}'`);
este router NUNCA lo posee, lo persiste ni lo loguea. `GET` no requiere
`graph_token` ni llama a Graph — el token del browser no está disponible en
un GET server-side normal (arquitectura preservada de Batch 1: el
transporte compartido no posee tokens).

Clasificación: OPERATIONS / STAFFING HTTP. NOT PLATFORM / SHARED. NO
importa `webapp.main`, no construye URLs de Graph, no posee el mapeo de
campos, no posee transporte Graph, no realiza cálculos de negocio — todo
eso vive en `StaffingService`/`StaffingRepository`/`core.microsoft365`
(Batch 1/2). Consume `webapp.operations_access` (política mínima y
provisoria, separada de `webapp.access`).

El deep-link no autenticado entra por `webapp.workspaces.platform_login_redirect`
(apunta a `/platform`, alias de compatibilidad que a su vez redirige a
`/onedanwor` — ver Batch A) — NO por `webapp.nav.login_redirect`
(Copilot-específico): Operaciones es un workspace distinto del Copiloto.

`_require_operations_session` distingue PÁGINA de ACCIÓN al armar `next`:
`staffing_page` (GET) no pasa `next_path` — `request.url.path` YA es la
página, corresponde tal cual. `staffing_data` (POST) pasa
`next_path="/operaciones/staffing/asignaciones"` EXPLÍCITO — sin sesión,
`next` debe apuntar a la PÁGINA, nunca al endpoint de datos que disparó
el redirect (el `graph_token` del POST original se descarta sin más:
nunca se lee, nunca se propaga a la URL de login — tras el login la
página real se re-inicializa y dispara su propio POST de HTMX fresco).
"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import operations_access, workspaces
from webapp.services import auth_service
from webapp.services.activity_service import log_verbose
from webapp.services.staffing_service import StaffingService, build_dashboard_payload
from webapp.templating import templates

router = APIRouter()


def _require_operations_session(request: Request, *, next_path: str | None = None):
    """Redirect (sin sesión) o 403 (sesión sin tier "full") — o None si OK.
    Compartido por GET y POST para no duplicar la regla de acceso.
    `next_path` explícito (solo lo pasa el POST de datos): ver docstring
    del módulo — sin sesión, `next` debe apuntar a la página, no al
    endpoint de acción que disparó el redirect."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request, next_path=next_path)
    if not operations_access.is_staffing_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Staffing")
    return None


@router.get("/operaciones/staffing/asignaciones", response_class=HTMLResponse, include_in_schema=False)
def staffing_page(request: Request):
    redirect = _require_operations_session(request)
    if redirect is not None:
        return redirect
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="operations/staffing.html",
        context={
            "current_workspace": workspaces.OPERATIONS,
            "available_workspaces": workspaces.available_workspaces(user),
            # Operaciones no tiene concepto de "pack" propio — la identidad
            # animada compartida (ver platform_topbar.html) es un asset físico
            # bajo content_packs/, así que se resuelve contra el pack por
            # defecto de la webapp (mismo mecanismo que ya usa Copiloto, sin
            # inventar un registro de packs para Operaciones).
            "pack": DEFAULT_PACK,
        },
    )


@router.post("/operaciones/staffing/asignaciones/data", response_class=HTMLResponse, include_in_schema=False)
def staffing_data(request: Request, graph_token: str = Form("")):
    redirect = _require_operations_session(request, next_path="/operaciones/staffing/asignaciones")
    if redirect is not None:
        return redirect

    if not graph_token:
        return templates.TemplateResponse(
            request=request, name="operations/staffing_dashboard.html", context={"state": "missing_token"},
        )

    user = auth_service.session_user(request)
    try:
        rows = StaffingService().get_overview(graph_token)
    except RuntimeError:
        # Config de Staffing incompleta (site_id / field mapping) — ver Batch 2/3.
        log_verbose("staffing_overview_config_incomplete", user=user)
        return templates.TemplateResponse(
            request=request, name="operations/staffing_dashboard.html", context={"state": "config_incomplete"},
        )
    except Exception as exc:  # noqa: BLE001 — falla de Graph/transporte, no de config
        log_verbose("staffing_overview_error", user=user, error_type=type(exc).__name__)
        return templates.TemplateResponse(
            request=request, name="operations/staffing_dashboard.html", context={"state": "graph_error"},
        )

    # Payload ÚNICO y normalizado (Asignaciones Dashboard v1) — el filtrado
    # de KPIs/timeline/disponibilidad/detalle ocurre 100% client-side (Alpine,
    # ver app.js::staffingDashboard) sobre este mismo array; no hay un
    # segundo endpoint de "datos filtrados" que vuelva a golpear Graph.
    payload = build_dashboard_payload(rows)
    return templates.TemplateResponse(
        request=request, name="operations/staffing_dashboard.html",
        context={"state": "ok", "payload": payload},
    )


# --- Compatibilidad histórica (nombres en inglés, Batch 3) ------------------
# 307 preserva method + body — el POST con `graph_token` en el form sigue
# llegando correctamente a la ruta canónica, HTMX incluido (el browser sigue
# la redirección de forma transparente). Nunca reimplementan la lógica de
# arriba: un solo punto de verdad.

@router.get("/operations/staffing", include_in_schema=False)
def staffing_page_compat():
    return RedirectResponse("/operaciones/staffing/asignaciones", status_code=307)


@router.post("/operations/staffing/data", include_in_schema=False)
def staffing_data_compat():
    return RedirectResponse("/operaciones/staffing/asignaciones/data", status_code=307)
