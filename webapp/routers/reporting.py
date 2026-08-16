"""
reporting.py — Operations / Reporting Integral V0 preview (ver HANDOFF).

`GET /operaciones/reporting/integral` — superficie de PREVISUALIZACIÓN
interna para que el owner revise el prototipo de consolidación del reporte
Power BI "Danwor - Tablero Integral" (12 páginas → 6 experiencias) dentro
del Hub real, no como HTML standalone. Deliberadamente mínima: sin Graph,
sin persistencia, sin fragmento HTMX — el dataset es mock y vive embebido
client-side (`webapp/static/reporting.js`, portado 1:1 del prototipo
standalone `prototypes/reporting-integral-v0/index.html`, que sigue siendo
la referencia de diseño). Un solo GET, sin POST — no hay nada que cargar
de una fuente externa todavía.

Agregada a `partials/operations_nav.html` como "02 Reporting" (Align with
Hub backbone corrective, ver HANDOFF) — YA es navegación real, no solo URL
directa. Sigue sin ser la ruta canónica final: el nombre/ubicación pueden
cambiar cuando Reporting deje de ser un preview.

Reusa el MISMO gate de autorización que Staffing
(`webapp.operations_access.is_operations_authorized` + sesión vía
`auth_service`) — NUNCA un mecanismo de auth nuevo. Mismo criterio que
`staffing_page`: sin `graph_token`, sin llamada a Graph en el GET (acá
directamente no hay ninguna, ni siquiera vía POST — todo el dato es mock
embebido en el JS servido).

Clasificación: OPERATIONS / REPORTING HTTP (preview). NOT PLATFORM /
SHARED. NO importa `webapp.main`, no posee transporte Graph, no define
lógica de negocio de Reporting (eso vive enteramente en
`webapp/static/reporting.js` mientras siga siendo mock — se moverá a un
service real cuando haya fuente de datos)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.content_registry import DEFAULT_PACK
from webapp import operations_access, workspaces
from webapp.services import auth_service
from webapp.services.reporting_repository import (
    ReportingConfigurationError,
    ReportingGraphError,
    ReportingSourceError,
)
from webapp.services.reporting_service import ReportingService
from webapp.templating import templates

router = APIRouter()
_logger = logging.getLogger("danwor.reporting")


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
    if not auth_service.session_user(request):
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
            cartera = {"proyecto": service.get_cartera_proyecto(graph_token)}
        else:
            cartera = service.get_cartera(graph_token)
    except ReportingConfigurationError:
        # ÚNICA condición de config incompleta (site/list ID sin setear). NUNCA
        # un RuntimeError genérico surgido más abajo (parseo/join/etc.).
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
    `/data/horas-vendidas/anios-disponibles`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios_list = [a.strip() for a in anios.split(",") if a.strip()] or None
        horas_vendidas = service.get_horas_vendidas(graph_token, anios=anios_list)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
    hidratación del tab. MISMO gate/token/fail-soft que `reporting_cartera_data`."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        anios = service.get_horas_vendidas_anios(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
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
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting participacion anios-disponibles: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", "aniosDisponibles": anios})


@router.post("/operaciones/reporting/integral/data/batch-b", include_in_schema=False)
def reporting_batch_b_data(request: Request, graph_token: str = Form("")):
    """Datos REALES normalizados de Horas Productivas (AMBAS perspectivas,
    Productivas Y No Asignadas — Reporting Integral Batch B, CSV de Time
    Logs Reports). UN endpoint consolidado (no 2 POSTs separados) porque
    ambas perspectivas comparten el mismo fetch/combine de origen
    (`ReportingService.get_batch_b` — descargar/parsear TODOS los CSV es
    costo real, a diferencia de un GET de lista SharePoint). MISMO gate/
    token/fail-soft que `reporting_cartera_data` — ver ese endpoint para el
    contrato de errores completo."""
    if not auth_service.session_user(request):
        return workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operations / Reporting")
    if not graph_token:
        return JSONResponse({"state": "missing_token"})

    service = ReportingService()
    try:
        batch_b = service.get_batch_b(graph_token)
    except ReportingConfigurationError:
        return JSONResponse({"state": "config_incomplete"})
    except ReportingGraphError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage, "http_status": exc.status_code})
    except ReportingSourceError as exc:
        return JSONResponse({"state": "source_error", "stage": exc.stage})
    except Exception:  # noqa: BLE001
        _logger.exception("reporting batch-b data: unexpected failure")
        return JSONResponse({"state": "source_error", "stage": "unexpected"})
    return JSONResponse({"state": "ok", **batch_b})
