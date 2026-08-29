"""
reporting.py — Operations / Reporting Integral (ver HANDOFF).

`GET /operaciones/reporting/integral` — renderiza SOLO el shell (tabs +
loader inicial vacíos, ver INITIAL LOAD CONTRACT en reporting.js): consolida
el reporte Power BI "Danwor - Tablero Integral" (12 páginas → 6 experiencias)
dentro del Hub real, no como HTML standalone. Ese GET no llama Graph ni
persiste nada — el dato real lo traen los POST `/data/*` de este mismo
router (cartera/rentabilidad/horas-vendidas/participación/batch-a/batch-b),
cada uno vía `ReportingRepository`/`ReportingService` con el token delegado
del browser (MISMO mecanismo que Staffing, NUNCA un cliente Graph nuevo por
endpoint). El dataset hardcodeado que vive en `webapp/static/reporting.js`
(portado 1:1 del prototipo standalone `prototypes/reporting-integral-v0/
index.html`, que sigue siendo la referencia de diseño congelada) es SOLO
estado inicial pre-hidratación — se reemplaza en el lugar apenas resuelve
el primer POST real, nunca coexiste con datos reales.

Agregada a `partials/operations_nav.html` como "02 Reporting" (Align with
Hub backbone corrective, ver HANDOFF) — navegación real, no solo URL directa.

Reusa el MISMO gate de autorización que Staffing
(`webapp.operations_access.is_operations_authorized` + sesión vía
`auth_service`) — NUNCA un mecanismo de auth nuevo. El GET en sí sigue sin
`graph_token`/llamada a Graph (mismo criterio que `staffing_page`); son los
POST de abajo los que reciben `graph_token` del browser y hacen el fetch
real.

Clasificación: OPERATIONS / REPORTING HTTP. NOT PLATFORM / SHARED. NO
importa `webapp.main`; el transporte Graph vive en
`webapp.services.reporting_repository`/`core.microsoft365` (nunca acá
directamente), la lógica de negocio/shaping en `webapp.services.
reporting_service` — este router solo conecta HTTP↔service, MISMO patrón
que `staffing.py`."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from datetime import datetime, timezone

from core.content_registry import DEFAULT_PACK
from webapp import operations_access, workspaces
from webapp.graph_auth_state import graph_error_state
from webapp.services import auth_service, feedback_store
from webapp.services.activity_context import AREA_REPORTING, SCOPE_OPERATIONS
from webapp.services.activity_service import is_verbose, log_activity, log_verbose
from webapp.services.reporting_repository import (
    ReportingConfigurationError,
    ReportingGraphError,
    ReportingSourceError,
)
from webapp.services.reporting_service import ReportingService
from webapp.templating import templates

router = APIRouter()
_logger = logging.getLogger("danwor.reporting")


def _graph_error_response(exc: ReportingGraphError) -> JSONResponse:
    """Falla de TRANSPORTE Graph → estado. UN solo punto para las 11 rutas
    de datos: antes cada `except` repetía el mismo literal `source_error`,
    así que un 401 —la autorización delegada vencida— era indistinguible de
    una caída real de SharePoint, y el usuario leía un error de fuente que
    era falso. La decisión vive en `webapp.graph_auth_state` (ver ahí por
    qué 401 sí y 403 no); acá sólo se le da forma HTTP.

    `http_status` se sigue mandando: era la ÚNICA pista que existía, y el
    frontend nunca la leía.

    `ReportingSourceError` (workbook inválido, tabla faltante) NO pasa por
    acá: no tiene status HTTP y es una falla de fuente de verdad.
    """
    return JSONResponse({
        "state": graph_error_state(exc.status_code),
        "stage": exc.stage, "http_status": exc.status_code,
    })


@router.get("/operaciones/reporting/integral", response_class=HTMLResponse, include_in_schema=False)
def reporting_integral_preview(request: Request):
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    user = auth_service.session_user(request)
    return templates.TemplateResponse(
        request=request, name="operations/reporting_integral.html",
        context={
            "current_workspace": workspaces.OPERATIONS,
            "available_workspaces": workspaces.available_workspaces(user),
            "pack": DEFAULT_PACK,
            "user": user,
            # Resalta "02 Reporting" en operations_nav.html.
            "active_page": "reporting",
        },
    )


@router.post("/operaciones/reporting/integral/data/cartera", include_in_schema=False)
def reporting_cartera_data(
    request: Request, graph_token: str = Form(""), perspectiva: str = Form(""), anios: str = Form(""),
):
    """Datos REALES normalizados de Cartera (Reporting Integral real-data Fase 1).
    MISMO gate de Operations + MISMO token delegado del browser que Staffing —
    sin auth ni cliente Graph nuevos. Fail-soft/localizado: cualquier falla
    devuelve HTTP 200 con `state != "ok"` (el renderer entra en su estado de
    error propio de Cartera; el resto de Reporting sigue usable). NUNCA expone
    el token, el JSON crudo de Graph ni internal names de SharePoint.

    `perspectiva` (P5, owner live QA de performance — Cartera ~17s):
    OPCIONAL. `"mantenimiento"`/`"proyecto"` disparan SOLO el fetch de esa
    perspectiva (`get_cartera_mantenimiento` NUNCA descarga/parsea la XLSX
    de Facturadas — Proyecto es la única que la necesita). Omitido (string
    vacío) preserva el contrato RETROCOMPATIBLE de siempre: AMBAS
    perspectivas en una sola respuesta (`get_cartera`, CON Facturadas).

    `anios` (HOT WINDOW / COLD HISTORY corrective, ver HANDOFF): SOLO
    aplica con `perspectiva=mantenimiento` — CSV de años (p. ej. "2024") a
    hidratar; vacío/omitido resuelve al default de ventana caliente (año
    actual + anterior, ver `ReportingService._default_hot_years`). Ignorado
    para `perspectiva=proyecto`/combo (Proyecto agrega la vida completa del
    proyecto, nunca acepta ventana — ver `get_cartera_proyecto`).
    `aniosDisponibles` (metadata de años fríos seleccionables) NO viaja acá
    a propósito — bundlearla haría que CADA request de Mantenimiento
    (incluida la ventana caliente por defecto, el camino más sensible a
    latencia) pague una lectura adicional de Horas por proyectos. Vive en
    `/data/cartera/anios-disponibles`, un endpoint separado que el
    frontend dispara en paralelo SIN bloquear el primer render — ver
    `reporting_cartera_anios_disponibles` abajo."""
    user = auth_service.session_user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        if perspectiva == "mantenimiento":
            anios_list = [a.strip() for a in anios.split(",") if a.strip()] or None
            cartera = {"mantenimiento": service.get_cartera_mantenimiento(graph_token, anios=anios_list)}
        elif perspectiva == "proyecto":
            # CACHE VERSIONED HISTORICAL TIME LOGS corrective (Fase 2, ver
            # HANDOFF + reporting_time_logs_cache.py): `cache_scope` es el
            # email de la sesión Hub YA verificada arriba (`user`) — NUNCA
            # `graph_token` (nunca se guarda/loguea/usa como key de cache).
            # `sub` (no `email`) es la claim real del JWT de sesión (ver
            # `auth_service.create_session`/`session_user`) — MISMO
            # fallback defensivo que el resto del Hub usa para mostrar el
            # usuario (`user.get("username") or user.get("email") or
            # user.get("sub")`, ver operations_access.py/main.py).
            cache_scope = str(user.get("username") or user.get("email") or user.get("sub") or "") or None
            cartera = {"proyecto": service.get_cartera_proyecto(graph_token, cache_scope=cache_scope)}
        else:
            cartera = service.get_cartera(graph_token)
    except ReportingConfigurationError:
        # ÚNICA condición de config incompleta (site/list ID sin setear). NUNCA
        # un RuntimeError genérico surgido más abajo (parseo/join/etc.).
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001 — inesperada: se loguea con traceback server-side
        # Fail-soft: el traceback real queda en el log del server (convención
        # `danwor.*`), NUNCA el texto/​token/​body crudo al browser.
        _logger.exception("reporting cartera data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "cartera": cartera})


@router.post("/operaciones/reporting/integral/data/cartera/anios-disponibles", include_in_schema=False)
def reporting_cartera_anios_disponibles(request: Request, graph_token: str = Form("")):
    """Metadata de año/estado de Mantenimiento (HOT WINDOW / COLD HISTORY
    corrective, ver HANDOFF) — SEPARADA de `reporting_cartera_data` a
    propósito: puebla el <select> de Año con años FRÍOS (fuera de la
    ventana caliente por defecto) sin sumarle costo a la respuesta rápida
    de Mantenimiento. El frontend la dispara en PARALELO durante la
    hidratación del tab Cartera (nunca bloquea el primer render, ver
    `hydrateCarteraTab` en reporting.js) — MISMO gate/token/fail-soft que
    `reporting_cartera_data`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios = service.get_cartera_mantenimiento_anios(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting cartera anios-disponibles: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "aniosDisponibles": anios})


@router.post("/operaciones/reporting/integral/data/rentabilidad", include_in_schema=False)
def reporting_rentabilidad_data(request: Request, graph_token: str = Form(""), anios: str = Form("")):
    """Datos REALES normalizados de Rentabilidad (AMBAS perspectivas,
    Resumen Y Certificadas vs Ejecutadas — Reporting Integral Batch B).
    Fetch propio: reusa `get_cartera_facts` (Certificadas/Vendidas,
    SharePoint) Y `get_time_logs_facts` (Trabajadas/Ejecutadas, CSV — MISMO
    adapter que Horas Productivas) — ver `ReportingService.get_rentabilidad`.
    MISMO gate/token/fail-soft que `reporting_cartera_data` — ver ese
    endpoint para el contrato de errores completo.

    `anios` (HOT WINDOW / COLD HISTORY corrective): MISMO contrato que
    `reporting_horas_vendidas_data` — CSV de años a hidratar; vacío/omitido
    resuelve al default de ventana caliente (año actual + anterior). La
    metadata de años seleccionables vive en
    `/data/rentabilidad/anios-disponibles`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios_list = [a.strip() for a in anios.split(",") if a.strip()] or None
        rentabilidad = service.get_rentabilidad(graph_token, anios=anios_list)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting rentabilidad data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "rentabilidad": rentabilidad})


@router.post("/operaciones/reporting/integral/data/rentabilidad/anios-disponibles", include_in_schema=False)
def reporting_rentabilidad_anios_disponibles(request: Request, graph_token: str = Form("")):
    """Metadata de año/estado de Rentabilidad — SEPARADA del fetch de datos
    a propósito (mismo motivo que Cartera/Horas Vendidas: puebla el
    <select> de Año con años FRÍOS sin sumarle costo a la respuesta rápida
    de la ventana caliente). El frontend la dispara en PARALELO durante la
    hidratación del tab. MISMO gate/token/fail-soft que
    `reporting_cartera_data`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios = service.get_rentabilidad_anios(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting rentabilidad anios-disponibles: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "aniosDisponibles": anios})


@router.post("/operaciones/reporting/integral/data/batch-a", include_in_schema=False)
def reporting_batch_a_data(request: Request, graph_token: str = Form("")):
    """Datos REALES normalizados de Calendario/Salud de Cliente (Reporting
    Integral Batch A — backbone reuse de Cartera, categóricas puras sobre
    `cliente_dim`, sin grano temporal). UN endpoint consolidado (no 2 POSTs
    separados) porque ambas experiencias comparten el mismo fetch de origen
    (`ReportingService.get_batch_a`). MISMO gate/token/fail-soft que
    `reporting_cartera_data`. Horas Vendidas Y Participación SALIERON de acá
    (owner directive — frontera de runtime, "no mantener Participación en
    full-history"): AMBAS tienen su propio endpoint con scope temporal
    (`reporting_horas_vendidas_data`/`reporting_participacion_data` abajo).
    Horas Productivas queda FUERA (Correction 1: CSV de Time Logs) — ver
    `reporting_batch_b_data`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        batch_a = service.get_batch_a(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting batch-a data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", **batch_a})


@router.post("/operaciones/reporting/integral/data/horas-vendidas", include_in_schema=False)
def reporting_horas_vendidas_data(request: Request, graph_token: str = Form(""), anios: str = Form("")):
    """Datos REALES de Horas Vendidas con SCOPE TEMPORAL (owner directive —
    HV pide al mismo source-family SÓLO su ventana caliente, sin arrastrar la
    historia completa que consume Participación). MISMO gate/token/fail-soft y
    MISMO contrato de `anios` que `reporting_cartera_data`:

    `anios` (HOT WINDOW / COLD HISTORY): CSV de años (p. ej. "2024") a
    hidratar; vacío/omitido resuelve al default de ventana caliente (año
    actual + anterior, `ReportingService._default_hot_years`). Una lista de un
    año es el lazy-load de un año FRÍO al elegirlo en el <select> de Año. La
    metadata de años seleccionables NO viaja acá (mismo motivo que Cartera:
    cada request de la ventana caliente pagaría una lectura extra) — vive en
    `/data/horas-vendidas/anios-disponibles`.

    REUSO DE ADQUISICIÓN DELTA corrective (ver HANDOFF): `cache_scope`
    resuelto desde la sesión Hub YA verificada arriba (`user`) — MISMO
    criterio exacto que `reporting_cartera_data` (perspectiva Proyecto) —
    NUNCA `graph_token`."""
    user = auth_service.session_user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios_list = [a.strip() for a in anios.split(",") if a.strip()] or None
        cache_scope = str(user.get("username") or user.get("email") or user.get("sub") or "") or None
        horas_vendidas = service.get_horas_vendidas(graph_token, anios=anios_list, cache_scope=cache_scope)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting horas-vendidas data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "horasVendidas": horas_vendidas})


@router.post("/operaciones/reporting/integral/data/horas-vendidas/anios-disponibles", include_in_schema=False)
def reporting_horas_vendidas_anios_disponibles(request: Request, graph_token: str = Form("")):
    """Metadata de año/estado de Horas Vendidas (todos los tipos de proyecto)
    — SEPARADA del fetch de datos a propósito (misma razón que Cartera: puebla
    el <select> de Año con años FRÍOS sin sumarle costo a la respuesta rápida
    de la ventana caliente). El frontend la dispara en PARALELO durante la
    hidratación del tab. MISMO gate/token/fail-soft que `reporting_cartera_data`.

    REUSO DE ADQUISICIÓN DELTA corrective (ver HANDOFF): `cache_scope`
    MISMO criterio que `reporting_horas_vendidas_data` arriba."""
    user = auth_service.session_user(request)
    if not user:
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        cache_scope = str(user.get("username") or user.get("email") or user.get("sub") or "") or None
        anios = service.get_horas_vendidas_anios(graph_token, cache_scope=cache_scope)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting horas-vendidas anios-disponibles: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "aniosDisponibles": anios})


@router.post("/operaciones/reporting/integral/data/participacion", include_in_schema=False)
def reporting_participacion_data(request: Request, graph_token: str = Form(""), anios: str = Form("")):
    """Datos REALES de Participación con SCOPE TEMPORAL (owner directive —
    "no mantener Participación en full-history solo porque el batch-a viejo
    lo hacía", MISMO principio que Horas Vendidas/Rentabilidad). MISMO gate/
    token/fail-soft y MISMO contrato de `anios` que `reporting_horas_
    vendidas_data`:

    `anios` (HOT WINDOW / COLD HISTORY, Año MULTI-SELECT): CSV de años (p. ej.
    "2024" o "2024,2023") a hidratar; vacío/omitido resuelve al default de
    ventana caliente (año actual + anterior, `ReportingService.
    _default_hot_years`). Una lista de un año es el lazy-load de un año FRÍO
    al tildarlo en el checkbox-group de Año — a diferencia de HV/Rentabilidad
    (Año single-select), acá el frontend puede pedir MÁS de un año FRÍO a la
    vez si el usuario tilda varios de un saque (ver `loadComercialParticipacionColdYears`
    en reporting.js). La metadata de años seleccionables NO viaja acá — vive
    en `/data/participacion/anios-disponibles`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios_list = [a.strip() for a in anios.split(",") if a.strip()] or None
        participacion = service.get_participacion(graph_token, anios=anios_list)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting participacion data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "participacion": participacion})


@router.post("/operaciones/reporting/integral/data/participacion/anios-disponibles", include_in_schema=False)
def reporting_participacion_anios_disponibles(request: Request, graph_token: str = Form("")):
    """Metadata de año/estado de Participación (todos los tipos) — SEPARADA
    del fetch de datos a propósito (misma razón que Cartera/Horas Vendidas:
    puebla el checkbox-group de Año con años FRÍOS sin sumarle costo a la
    respuesta rápida de la ventana caliente). El frontend la dispara en
    PARALELO durante la hidratación del tab. MISMO gate/token/fail-soft que
    `reporting_cartera_data`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios = service.get_participacion_anios(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting participacion anios-disponibles: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "aniosDisponibles": anios})


@router.post("/operaciones/reporting/integral/data/batch-b", include_in_schema=False)
def reporting_batch_b_data(request: Request, graph_token: str = Form(""), anios: str = Form("")):
    """Datos REALES normalizados de Horas Productivas (AMBAS perspectivas,
    Productivas Y No Asignadas — Reporting Integral Batch B, CSV de Time
    Logs Reports). UN endpoint consolidado (no 2 POSTs separados) porque
    ambas perspectivas comparten el mismo fetch/combine de origen
    (`ReportingService.get_batch_b` — descargar/parsear TODOS los CSV es
    costo real, a diferencia de un GET de lista SharePoint). MISMO gate/
    token/fail-soft que `reporting_cartera_data` — ver ese endpoint para el
    contrato de errores completo.

    `anios` (HOT WINDOW / COLD HISTORY corrective, owner decision
    2026-08-18): CSV de años (p. ej. "2024") a hidratar; vacío/omitido
    resuelve al default de ventana caliente (año actual + anterior, ver
    `ReportingService._default_hot_years`) — MISMO contrato que `anios` de
    `reporting_cartera_data`. `aniosDisponibles` (metadata de años fríos
    seleccionables) NO viaja acá a propósito, MISMO motivo que Cartera —
    vive en `/data/batch-b/anios-disponibles`, disparado en paralelo sin
    bloquear el primer render."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios_list = [a.strip() for a in anios.split(",") if a.strip()] or None
        batch_b = service.get_batch_b(graph_token, anios=anios_list)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting batch-b data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", **batch_b})


@router.post("/operaciones/reporting/integral/data/batch-b/anios-disponibles", include_in_schema=False)
def reporting_batch_b_anios_disponibles(request: Request, graph_token: str = Form("")):
    """Metadata de año de Horas Productivas (HOT WINDOW / COLD HISTORY
    corrective) — MISMO criterio que `reporting_cartera_anios_disponibles`:
    endpoint separado y liviano (solo lista archivos por metadata Graph,
    nunca descarga/parsea CSV) para no pagar ese costo en cada request de
    la ventana caliente."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios_disponibles = service.get_batch_b_anios(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return _graph_error_response(exc)
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting batch-b anios-disponibles: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "aniosDisponibles": anios_disponibles})


# ── Feedback + telemetría de Reporting (Shared Hub Feedback + Activity) ──────
# Reporting es SPA client-side (reporting.js), no HTMX — así que reusa las
# CAPACIDADES compartidas (mismo store de feedback, mismo pipeline de Activity)
# vía endpoints JSON, en vez del intercambio de partials de Copilot. Ver ticket
# CLOSE REPORTING §A. El feedback es "un control por MAIN TAB".

# Títulos canónicos de las 6 experiencias (main tabs) — misma taxonomía que
# `EXPERIENCES` en reporting.js. `report_tab` del cliente se valida contra esto
# (nunca se persiste/loguea un tab arbitrario inyectado).
_REPORT_TABS: dict[str, str] = {
    "cartera": "Cartera / Proyectos",
    "rentabilidad": "Rentabilidad",
    "horasvendidas": "Horas Vendidas",
    "comercial": "Participación / Comercial",
    "calendario": "Calendario",
    "capacidad": "Horas Productivas",
}

# Eventos de Activity de Reporting permitidos desde el beacon de cliente
# (whitelist — MISMO criterio de seguridad que `telemetry.py`: el cliente NUNCA
# fija scope/area; los fija el servidor a operations/reporting). Semánticos, no
# "cada click del DOM".
_REPORTING_TELEMETRY_EVENTS = {
    "reporting.tab_view", "reporting.perspective_change", "reporting.filter_change",
    "reporting.filter_clear", "reporting.cold_year_load", "reporting.hierarchy_toggle",
    "reporting.expand_all", "reporting.collapse_all", "reporting.sort_change",
    "reporting.refresh",
}


def _reporting_gate(request: Request):
    """Gate compartido de los endpoints de Reporting (sesión + autorización
    Operations) — MISMO criterio que los endpoints de datos. Devuelve una
    respuesta si hay que cortar, o `None` si puede continuar."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    return None


@router.post("/operaciones/reporting/integral/feedback", include_in_schema=False)
def reporting_feedback(request: Request, report_tab: str = Form(""), rating: str = Form(""),
                       comment: str = Form("")):
    """Feedback "¿Qué te pareció este reporte?" al cierre de cada main tab.
    Reusa el store COMPARTIDO (`feedback_store` → `FEEDBACK_LOG_DIR`, mismo
    formato/analítica que Copilot: campo `type`) y emite un evento del MISMO
    pipeline de Activity (`log_activity('reporting.feedback', ...)`). NO crea
    almacenamiento propio de Reporting. Best-effort: nunca rompe la vista."""
    blocked = _reporting_gate(request)
    if blocked is not None:
        return blocked
    tab = (report_tab or "").strip()
    if rating not in {"up", "down"} or tab not in _REPORT_TABS:
        raise HTTPException(400, "Feedback inválido")
    user = auth_service.session_user(request) or {}
    now = datetime.now(timezone.utc)
    payload = {
        "type": "reporting_report",
        "report_tab": tab,
        "report_title": _REPORT_TABS[tab],
        "rating": rating,
        "user": str(user.get("username") or user.get("email") or user.get("sub") or ""),
        "ts": now.isoformat(),
    }
    clean_comment = (comment or "").strip()
    if clean_comment:
        payload["comment"] = clean_comment
    feedback_store.write_feedback_record(payload, prefix="reporting_feedback", stamp=now.strftime("%Y%m%d_%H%M%S"))
    # Evento del pipeline de Activity del Hub (mismo sink que Copilot/Staffing).
    # Metadata semántica: report_tab + sentimiento (nunca valores de negocio).
    log_activity(
        "reporting.feedback", user=user, scope=SCOPE_OPERATIONS, area=AREA_REPORTING,
        module="reporting", report_tab=tab,
        feedback_sentiment=("positive" if rating == "up" else "negative"),
    )
    return JSONResponse({"state": "ok"})


@router.post("/operaciones/reporting/integral/telemetry", include_in_schema=False)
async def reporting_telemetry(request: Request):
    """Beacon liviano de interacción de Reporting (tab view, cambio de
    perspectiva/filtro, año frío, expandir/contraer, sort). MISMO pipeline y
    MISMO modelo de seguridad que `telemetry.py`: solo VERBOSE, 204 siempre, y
    el CLIENTE nunca fija scope/area — los fija el servidor a operations/
    reporting. Se filtra por whitelist de eventos semánticos."""
    if not is_verbose():
        return JSONResponse({"state": "ok"}, status_code=204)
    if not auth_service.session_user(request):
        return JSONResponse({"state": "ok"}, status_code=204)
    if not operations_access.is_operations_authorized(request):
        return JSONResponse({"state": "ok"}, status_code=204)
    user = auth_service.session_user(request)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"state": "ok"}, status_code=204)
    event = str((data or {}).get("event") or "").strip()
    if event in _REPORTING_TELEMETRY_EVENTS:
        # Se excluyen event/scope/area del body para que el cliente no pueda
        # inyectar/colisionar el contexto de plataforma (keyword-only en
        # log_verbose). `report_tab`/`perspective`/`filter_name`/`load_scope`
        # viajan como payload semántico.
        payload = {k: v for k, v in (data or {}).items() if k not in ("event", "scope", "area", "module")}
        log_verbose(event, user=user, scope=SCOPE_OPERATIONS, area=AREA_REPORTING, module="reporting", **payload)
    return JSONResponse({"state": "ok"}, status_code=204)
