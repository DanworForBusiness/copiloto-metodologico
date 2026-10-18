"""
catalog_assessment.py — Assessment de modelado dentro del Catálogo.

Rutas (todas exigen sesión, igual que el detalle del Catálogo; sin rol nuevo):

  GET  /catalog/flow/assessment        informe o estado, SOLO desde la caché. NUNCA genera ni escribe.
  POST /catalog/flow/assessment        generar / regenerar / reintentar; devuelve el informe estructurado.
  GET  /catalog/flow/assessment.json   el perfil canónico validado exacto que usa el UI.
  GET  /catalog/flow/assessment.md     ``render_markdown(perfil)`` del MISMO perfil.

  GET  /catalog/client/assessment       consolidado del CLIENTE del flujo ``flow`` (fragmento HTMX; "← Volver" regresa a ese flujo)
  GET  /catalog/client/assessment.json  el MISMO snapshot como JSON
  GET  /catalog/client/assessment.md    el MISMO snapshot como Markdown
                                        Solo LECTURA: jamás conectan a Datasphere ni ejecutan/generan assessments (ver
                                        ``client_assessment_portfolio``). No hay POST.

Política de acceso (verificada contra el repo, no supuesta): ``readonly`` (tier academy) significa SIN IA —
no hay asistente ni análisis de sección—; el Catálogo es "determinístico y sin tokens" y disponible para
cualquier usuario logueado, y ``POST /catalog/regenerate`` ya acepta a cualquier usuario logueado. El
assessment es determinista, no consume LLM y solo produce una caché operativa derivada: por eso generar NO
se bloquea para el perfil readonly. El POST, además, exige mismo origen (``same_origin_guard``), porque con
cookies de sesión ``SameSite=None`` es una acción que consume CPU y no debe poder forzarse desde otro sitio.

Regla de dependencia: NO importa ``webapp.main`` (main incluye este router). La resolución de entradas usa
``catalog_reads._catalog_entries`` (la misma fuente del Catálogo).
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from core.content_registry import DEFAULT_PACK, resolve_pack
from webapp import access, nav, same_origin_guard
from webapp.services import assessment_presenter as presenter
from webapp.services import governed_decisions_presenter as gd_presenter
from webapp.services import assessment_service as svc
from webapp.services import auth_service, catalog_reads
from webapp.services import client_assessment_portfolio as portfolio
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity, log_verbose
from webapp.templating import templates

router = APIRouter()


def _require_user(request: Request) -> dict:
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return user


def _entry_or_404(flow: str):
    mod, entries = catalog_reads._catalog_entries()
    entry = mod.find_flow_by_id(entries, flow) if (mod and flow) else None
    if entry is None:
        raise HTTPException(404, "Flujo no encontrado en el catálogo")
    return entry


def _pack(request: Request, pack: str) -> str:
    return resolve_pack(pack) if access.is_diagnostic(request) else DEFAULT_PACK


def _report_response(request: Request, status: svc.AssessmentStatus, entry, pack: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="partials/catalog_assessment_report.html",
        context={"v": presenter.report_view(status, entry, pack), "pack": pack,
                 "governed_contracts": gd_presenter.governed_contract_links(status.profile, status.client, pack)})


@router.get("/catalog/flow/assessment", response_class=HTMLResponse, include_in_schema=False)
def assessment_get(request: Request, flow: str = "", pack: str = Query(DEFAULT_PACK)):
    """Estado/informe desde la caché. Un miss de caché es ``NOT_YET_ASSESSED``: este GET JAMÁS ejecuta el motor."""
    user = auth_service.session_user(request)
    if not user:
        if request.headers.get("HX-Request"):
            raise HTTPException(401, "Sin sesión")
        return nav.login_redirect(request, _pack(request, pack))
    selected = _pack(request, pack)
    entry = _entry_or_404(flow)
    log_verbose("catalog_view", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="assessment", ref=entry.entry_id)
    status = svc.status_for_entry(entry)
    if not request.headers.get("HX-Request"):          # el informe es un fragmento del panel; navegación directa → detalle del flujo
        return RedirectResponse(presenter.flow_url(entry, selected), status_code=303)
    return _report_response(request, status, entry, selected)


@router.post("/catalog/flow/assessment", response_class=HTMLResponse, include_in_schema=False)
def assessment_generate(request: Request, flow: str = "", pack: str = Query(DEFAULT_PACK)):
    """Generar / regenerar / reintentar. Idempotente sobre la vigencia (CURRENT no recalcula) y single-flight
    por (cliente, AM). Devuelve el informe estructurado resultante (o el estado de falla estable)."""
    user = _require_user(request)
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen no permitido")
    selected = _pack(request, pack)
    entry = _entry_or_404(flow)
    status = svc.generate(entry)
    log_activity("catalog_assessment_generate", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                 flow=entry.entry_id, state=status.state, error_code=status.error_code)
    return _report_response(request, status, entry, selected)


def _export(request: Request, flow: str, ext: str) -> Response:
    _require_user(request)
    entry = _entry_or_404(flow)
    status = svc.status_for_entry(entry)
    if not status.viewable:
        raise HTTPException(404, "Este flujo no tiene un assessment disponible para exportar")
    if ext == "json":
        body, media = svc.export_json(status), "application/json; charset=utf-8"
    else:
        body, media = svc.export_markdown(status), "text/markdown; charset=utf-8"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{svc.export_filename(status, ext)}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/catalog/flow/assessment.json", include_in_schema=False)
def assessment_json(request: Request, flow: str = ""):
    return _export(request, flow, "json")


@router.get("/catalog/flow/assessment.md", include_in_schema=False)
def assessment_markdown(request: Request, flow: str = ""):
    return _export(request, flow, "md")


# ═══════════════════ consolidado del cliente (solo lectura) ═══════════════════
@router.get("/catalog/client/assessment", response_class=HTMLResponse, include_in_schema=False)
def client_assessment_get(request: Request, flow: str = "", pack: str = Query(DEFAULT_PACK), origin: str = ""):
    """Assessment del cliente: lee las entradas del Catálogo del cliente del flujo ``flow`` y su estado canónico (caché). NUNCA genera.
    ``origin=assistant`` (único valor admitido): se abrió desde el asistente con un flujo de REFERENCIA técnica; «← Volver» cierra el detalle y restaura el módulo."""
    user = auth_service.session_user(request)
    if not user:
        if request.headers.get("HX-Request"):
            raise HTTPException(401, "Sin sesión")
        return nav.login_redirect(request, _pack(request, pack))
    selected = _pack(request, pack)
    entry = _entry_or_404(flow)
    if not request.headers.get("HX-Request"):          # el consolidado es un fragmento del panel; navegación directa → detalle del flujo
        return RedirectResponse(presenter.flow_url(entry, selected), status_code=303)
    log_verbose("catalog_view", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="client_assessment", ref=entry.cliente)
    snap = portfolio.build_snapshot(entry, selected)
    q = {"flow": entry.entry_id}
    return templates.TemplateResponse(
        request=request, name="partials/client_assessment_report.html",
        context={"v": portfolio.report_view(snap), "pack": selected, "back_url": presenter.flow_url(entry, selected), "back_close": origin == "assistant",
                 "json_url": "/catalog/client/assessment.json?" + urlencode(q), "md_url": "/catalog/client/assessment.md?" + urlencode(q)})


def _client_export(request: Request, flow: str, ext: str) -> Response:
    _require_user(request)
    snap = portfolio.build_snapshot(_entry_or_404(flow), DEFAULT_PACK)
    if ext == "json":
        body, media = portfolio.to_json(snap), "application/json; charset=utf-8"
    else:
        body, media = portfolio.to_markdown(snap), "text/markdown; charset=utf-8"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{portfolio.export_filename(snap, ext)}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/catalog/client/assessment.json", include_in_schema=False)
def client_assessment_json(request: Request, flow: str = ""):
    return _client_export(request, flow, "json")


@router.get("/catalog/client/assessment.md", include_in_schema=False)
def client_assessment_markdown(request: Request, flow: str = ""):
    return _client_export(request, flow, "md")
