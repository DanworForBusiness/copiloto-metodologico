"""
governed_contracts_ui.py — Vista del Hub "Contratos Gobernados" (v4.B / B4): CONSULTA read-only de ratificaciones/revocaciones de
contratos CDS/API, por cliente (listado) y por contrato (detalle), reusando el backend B3.

Fronteras (D4/D6/D8): la ratificación pertenece a la CDS/API (no al Assessment ni a su Judgment); los consumidores son
TRAZABILIDAD, no sujetos aprobados. Esta vista es SOLO LECTURA (GET): nunca escribe eventos ni toca Assessments. Las acciones
Ratificar/Revocar (owner-only) las ejecuta el navegador contra las rutas POST de B3 (`/governed-decisions/ratify|revoke`) — acá
sólo se renderiza el formulario cuando el usuario es APP_OWNER; la autorización REAL la impone el backend B3.

Acceso de LECTURA: cualquier usuario autenticado (Política B, ver B4_ACCESS_MODEL.md) — misma visibilidad que los informes de
Assessment de donde deriva la evidencia. NO introduce permisos por cliente ni RBAC nuevo.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from core import remote_store
from core.content_registry import DEFAULT_PACK, content_pack_short_label, resolve_pack
from webapp import access
from webapp.services import auth_service
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_inventory as inventory
from webapp.services import governed_decisions_ops as ops
from webapp.services import governed_decisions_presenter as presenter
from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_verbose
from webapp.services.content_service import academy_asset_for, list_academy, list_appendices, list_modules
from webapp.templating import templates
from webapp import workspaces

router = APIRouter()


def _require_user(request: Request) -> dict:
    """CONSULTA: sólo exige sesión (Política B). La escritura owner-only vive en el router B3."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    return user


def _pack(request: Request, pack: str) -> str:
    return resolve_pack(pack) if access.is_diagnostic(request) else DEFAULT_PACK


def _shell(request: Request, user: dict, pack: str, **extra) -> dict:
    """Contexto de la cáscara del Copiloto (mismo que las páginas del Catálogo), + extra de la vista."""
    academy = [a for a in list_academy(pack) if not a.nav_hidden]
    ctx = {
        "modules": list_modules(pack),
        "appendices": [a for a in list_appendices(pack) if not a.nav_hidden],
        "academy": academy,
        "academy_assets": {a.stem: academy_asset_for(a.stem, pack) for a in academy},
        "active_group": "", "active_stem": "",
        "readonly": access.is_readonly(request),
        "pack": pack, "pack_label": content_pack_short_label(pack), "user": user,
        "current_workspace": workspaces.COPILOT,
        "available_workspaces": workspaces.available_workspaces(user),
        "is_owner": access.is_owner_user(user),
    }
    ctx.update(extra)
    return ctx


def _identity(remote_connection: str, remote_entity: str) -> dict:
    conn = (remote_connection or "").strip()
    return {"remote_connection": conn or None, "remote_entity": (remote_entity or "").strip()}


@router.get("/governed-contracts", response_class=HTMLResponse, include_in_schema=False)
def governed_contracts(request: Request, client: str = "", pack: str = Query(DEFAULT_PACK)):
    """Listado de contratos gobernados de un cliente (single-pass). Sin `client`: muestra el selector y un estado vacío."""
    user = _require_user(request)
    selected = _pack(request, pack)
    client = (client or "").strip()
    log_verbose("governed_contracts_list", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="list", ref=client)
    clients = inventory.list_clients()
    contracts: list[dict] = []
    error = None
    if client:
        try:
            rows = inventory.list_client_contracts(client, datetime.now(timezone.utc))
            contracts = [{**r, "chips": presenter.dimension_chips(r)} for r in rows]
        except ops.ContractEvidenceUnverifiable:
            error = "Hay al menos un Assessment gobernado de este cliente que no se puede leer/validar; no se puede listar con garantía de consistencia."
        except Exception:                                          # noqa: BLE001 — degradar sin filtrar internals
            error = "No se pudo resolver el inventario de contratos de este cliente."
    view = {"clients": clients, "selected_client": client, "contracts": contracts, "list_error": error}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/governed_contracts_list.html", context={"pack": selected, **view})
    return templates.TemplateResponse(request=request, name="governed_contracts.html",
                                      context=_shell(request, user, selected, mode="list", **view))


@router.get("/governed-contracts/detail", response_class=HTMLResponse, include_in_schema=False)
def governed_contract_detail(request: Request, client: str = "", remote_entity: str = "",
                             remote_connection: str = "", pack: str = Query(DEFAULT_PACK)):
    """Detalle de UN contrato: identidad, 3 dimensiones, evidencia observada (+límites), consumidores (trazabilidad) e historial."""
    user = _require_user(request)
    selected = _pack(request, pack)
    client = (client or "").strip()
    if not client or not (remote_entity or "").strip():
        raise HTTPException(404, "Contrato no especificado")
    identity = _identity(remote_connection, remote_entity)
    log_verbose("governed_contracts_detail", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, kind="detail",
                ref=gd.cds_technical_id(identity))
    now = datetime.now(timezone.utc)
    try:
        recon = ops.status(client, identity, now)                  # decisión/evidencia/revisión + historial + consumidores (SOLO LECTURA)
    except remote_store.RemoteReadError:
        if remote_store.read_enabled():
            raise HTTPException(503, "Registro de decisiones no disponible temporalmente")   # outage real: NO falsear "sin decisión"
        # Entorno sin registro durable de decisiones (p. ej. dev local): evidencia sin eventos → decisión NONE (seguro).
        try:
            res = ops.resolve_cds_contract(client, identity)
            digest, consumers = res["digest"], res["consumers"]
        except ops.GovernedOpsError:
            digest, consumers = None, []
        recon = {"subject": {"client": client, "technical_id": gd.cds_technical_id(identity)},
                 "events": [], "status": gd.derive_status([], digest, now),
                 "consumers": consumers, "evidence_digest": digest}
    chips = presenter.dimension_chips(recon["status"])
    # Evidencia OBSERVADA (slice) para el panel de evidencia; si no se puede resolver, se informa el motivo (no rompe la vista).
    observed, observed_note = None, None
    try:
        observed = ops.resolve_cds_contract(client, identity)["slice"]
    except ops.ContractEvidenceInsufficient:
        observed_note = "Sin evidencia de contrato para esta identidad en los Assessments del cliente."
    except ops.ContractEvidenceAmbiguous:
        observed_note = "La evidencia difiere entre los Assessments consumidores (contradictoria): no verificable."
    except ops.ContractEvidenceUnverifiable:
        observed_note = "Un Assessment del cliente no se puede leer/validar: evidencia no verificable."
    view = {
        "client": client, "identity": identity, "subject": recon["subject"], "chips": chips,
        "status": recon["status"], "evidence_digest": recon.get("evidence_digest"),
        "consumers": recon.get("consumers", []), "events": recon["events"],
        "observed": observed, "observed_note": observed_note,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/governed_contract_detail.html",
                                          context={"pack": selected, "is_owner": access.is_owner_user(user), **view})
    return templates.TemplateResponse(request=request, name="governed_contracts.html",
                                      context=_shell(request, user, selected, mode="detail", **view))
