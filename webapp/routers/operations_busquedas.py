"""
operations_busquedas.py — Operaciones / Búsquedas (REG-CH-01, I2).

Operaciones origina, consulta y gestiona SUS solicitudes de perfil dirigidas a
Capital Humano. NO hay repositorio ni almacenamiento propios: toda lectura y
escritura pasa por el servicio de I1 (`human_capital_service`: `create_solicitud`,
`edit_solicitud`, `resubmit_solicitud`, `cancel_solicitud`, `list_solicitudes_view`,
`get_solicitud_view`), que persiste en la entidad compartida `Búsqueda`.

Seguridad (TODAS las rutas, lectura y escritura):
  1. Sesión: sin sesión → redirect al login (GET) / 401 (POST).
  2. `operations_access.is_operations_authorized` (la MISMA frontera que Staffing y
     Reporting): cualquier usuario de Operaciones habilitado ve y gestiona las
     solicitudes; el resto → 403. Sin esto no se llega ni al 404.
  3. POST: `same_origin_guard.is_same_origin_write` (anti-CSRF mínimo del Hub).
  4. Proyección en lista blanca (`solicitud_view`): nunca candidatos, postulaciones,
     datos personales, JD ni objetos internos de CCHH. Una búsqueda sin solicitud
     (histórica / alta directa de CCHH) responde 404 como si no existiera.
El actor que se registra en el historial es el claim `sub` de la sesión.

Clasificación: OPERATIONS / BÚSQUEDAS HTTP. NO toca las pantallas de CCHH (I3)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.content_registry import DEFAULT_PACK
from webapp import hub_metrics_access, operations_access, same_origin_guard, workspaces
from webapp.services import auth_service, busqueda_repository, human_capital_catalog_repository, human_capital_service, pagination, search_text
from webapp.templating import templates

router = APIRouter()
_logger = logging.getLogger("danwor.operations.busquedas")

BASE_PATH = "/operaciones/busquedas"

# Errores de persistencia / resultado remoto indeterminado: el usuario debe poder reintentar sin duplicar.
_STORAGE_ERRORS = (
    busqueda_repository.BusquedaConfigIncompleteError,
    busqueda_repository.BusquedaRemoteOutcomeUnknownError,
    busqueda_repository.BusquedaStorageError,
)
_MSG_STORAGE = "No pudimos guardar o leer la solicitud en este momento. Reintentá en unos minutos; si ya la enviaste, no se va a duplicar."
_MSG_UNKNOWN_OUTCOME = "No pudimos confirmar si la solicitud quedó guardada. Reintentá el envío: se reconoce el mismo envío y no se duplica."
_MSG_CONCURRENT = "La solicitud cambió desde que abriste la pantalla (por ejemplo, Capital Humano la revisó). Volvé a abrirla para ver su estado actual antes de modificarla."
_MSG_SOURCE = "No pudimos leer las solicitudes en este momento. Reintentá en unos minutos."

# ---- Indicadores derivados del contrato de I1 (no son estados nuevos) ---------------------------------------------------------------------------------------------
INDICADORES = {
    "nueva": "Nueva",
    "reenviada": "Reenviada",
    "observada": "Observada",
    "en_gestion": "En gestión",
    "cancelada": "Cancelada",
    "cerrada": "Cerrada",
}
FILTRO_ESTADOS = tuple(INDICADORES.items())
# Badge: primitivo del Hub (`.status-badge`, tools.css) con SU paleta restringida (neutro / ámbar / verde / gris); la distinción fina la da el rótulo.
BADGE_TONOS = {"nueva": "otro", "reenviada": "otro", "observada": "tentativo", "en_gestion": "activo", "cancelada": "inactivo", "cerrada": "inactivo"}
# Cabecera del detalle: MISMO badge que los informes del Catálogo (`.catalog-card__verdict--*`: plena = verde, buena = ámbar, parcial = naranja, neutra = gris).
VEREDICTO_TONOS = {"nueva": "neutra", "reenviada": "neutra", "observada": "parcial", "en_gestion": "plena", "cancelada": "neutra", "cerrada": "neutra"}
_OK_MESSAGES = {
    "enviada": "Solicitud enviada a Capital Humano. Quedó registrada con estado «Enviada».",
    "editada": "Cambios guardados. La solicitud sigue en estado «Enviada».",
    "reenviada": "Solicitud corregida y reenviada a Capital Humano.",
    "cancelada": "Solicitud cancelada.",
    "eliminada": "Solicitud eliminada.",
}
_MSG_NOT_DELETABLE = "Esta solicitud ya no se puede eliminar: Capital Humano la tomó en gestión o tiene un proceso asociado. Volvé a abrirla para ver su estado actual."

DEDICACION_LABELS = {"horas_por_mes": "Horas por mes", "horas_por_semana": "Horas por semana", "porcentaje_jornada": "% de jornada"}
TIPO_LABELS = {"interna": "Necesidad interna", "proyecto": "Proyecto"}
DURACION_LABELS = {"meses": "Meses", "indefinida": "Indefinida"}
TRANSICION_LABELS = {
    "enviar": "Enviada a Capital Humano", "editar": "Editada por Operaciones", "observar": "Observada por Capital Humano",
    "reenviar": "Corregida y reenviada", "tomar": "Tomada por Capital Humano", "cancelar": "Cancelada por Operaciones",
    "proceso_eliminado": "Proceso eliminado por Capital Humano",
}


def indicador_of(view: dict) -> str:
    """Indicador visual de una solicitud, derivado SOLO del contrato de I1 (`estado_solicitud`, `estado`, `reenviada`)."""
    estado = view.get("estado_solicitud")
    if estado == busqueda_repository.SOLICITUD_CANCELADA:
        return "cancelada"
    if estado == busqueda_repository.SOLICITUD_EN_GESTION:
        return "cerrada" if view.get("estado") == "cerrado" else "en_gestion"
    if estado == busqueda_repository.SOLICITUD_OBSERVADA:
        return "observada"
    return "reenviada" if view.get("reenviada") else "nueva"


def _fold(text) -> str:
    return search_text.fold(text)                       # UNA sola normalización de búsqueda libre para toda la vertical (sin mayúsculas ni tildes)


def _fmt_date(value) -> str:
    """AAAA-MM-DD → DD/MM/AAAA (fechas de negocio; inválido → tal cual / vacío)."""
    if not isinstance(value, str) or len(value) < 10 or value[4] != "-" or value[7] != "-":
        return value or ""
    return f"{value[8:10]}/{value[5:7]}/{value[0:4]}"


def _fmt_dt(value) -> str:
    """Timestamp técnico (UTC ISO) → «DD/MM/AAAA HH:MM» en hora de Buenos Aires."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value or ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(busqueda_repository.BUSINESS_TZ).strftime("%d/%m/%Y %H:%M")


templates.env.filters["ops_date"] = _fmt_date
templates.env.filters["ops_datetime"] = _fmt_dt


# ---- Autorización (server-side, en TODAS las rutas) -----------------------------------------------------------------------------------------------------------------------
def _authorize_read(request: Request):
    """(user, None) si OK; (None, RedirectResponse) sin sesión; 403 si no es de Operaciones."""
    user = auth_service.session_user(request)
    if not user:
        return None, workspaces.platform_login_redirect(request)
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operaciones / Búsquedas")
    return user, None


def _authorize_write(request: Request) -> tuple[dict, str]:
    """(user, actor). 401 sin sesión, 403 sin Operaciones, 403 si el origen no es el mismo sitio."""
    user = auth_service.session_user(request)
    if not user:
        raise HTTPException(401, "Sin sesión")
    if not operations_access.is_operations_authorized(request):
        raise HTTPException(403, "No autorizado para Operaciones / Búsquedas")
    if not same_origin_guard.is_same_origin_write(request):
        raise HTTPException(403, "Origen del request no verificado")
    actor = str(user.get("sub") or "").strip().lower()
    if not actor:
        raise HTTPException(403, "La sesión no identifica al usuario")
    return user, actor


def _base_context(request: Request, user: dict, **extra) -> dict:
    return {
        "current_workspace": workspaces.OPERATIONS,
        "available_workspaces": workspaces.available_workspaces(user),
        "pack": DEFAULT_PACK,
        "user": user,
        "active_page": "busquedas",
        "can_hub_metrics": (
            hub_metrics_access.is_vertical_metrics_enabled("operations")
            and hub_metrics_access.can_access_hub_metrics(user)
        ),
        **extra,
    }


# ---- Formulario REG-CH-01 -------------------------------------------------------------------------------------------------------------------------------------------------------------
_FORM_KEYS = (
    "nombre_puesto", "tipo_busqueda", "proyecto", "cantidad_recursos", "dedicacion_valor", "dedicacion_unidad", "duracion_tipo", "duracion_meses",
    "area_consultora", "ubicacion", "modalidad", "dias_presenciales", "lugar_trabajo", "responsabilidades", "skills_excluyentes",
    "habilidades_blandas", "ingles", "certificaciones", "tipo_contrato", "fecha_inicio_prevista", "comentarios",
)


def _form_values_from_view(view: dict) -> dict:
    """Aplana el `requerimiento` guardado a los nombres de campo del formulario (edición / corrección)."""
    r = view.get("requerimiento") or {}
    pres = r.get("presencialidad") or {}
    ded = r.get("dedicacion") or {}
    dur = r.get("duracion") or {}
    return {
        "nombre_puesto": r.get("nombre_puesto"), "tipo_busqueda": r.get("tipo_busqueda"), "proyecto": r.get("proyecto"),
        "cantidad_recursos": r.get("cantidad_recursos"), "dedicacion_valor": ded.get("valor"), "dedicacion_unidad": ded.get("unidad"),
        "duracion_tipo": dur.get("tipo"), "duracion_meses": dur.get("meses"), "area_consultora": r.get("area_consultora"),
        "ubicacion": r.get("ubicacion"), "modalidad": r.get("modalidad"), "dias_presenciales": pres.get("dias"), "lugar_trabajo": pres.get("lugar"),
        "responsabilidades": r.get("responsabilidades"), "skills_excluyentes": r.get("skills_excluyentes"),
        "habilidades_blandas": r.get("habilidades_blandas"), "ingles": r.get("ingles"), "certificaciones": r.get("certificaciones"),
        "tipo_contrato": r.get("tipo_contrato"), "fecha_inicio_prevista": r.get("fecha_inicio_prevista"), "comentarios": r.get("comentarios"),
        "seniority": list(r.get("seniority") or []),
    }


def _legacy_option(selected: str | None, active: list[str]) -> bool:
    return bool(selected) and selected not in active


def _form_context(request: Request, user: dict, *, mode: str, values: dict, submission_id: str | None = None, view: dict | None = None,
                  error: str | None = None, warning: str | None = None, status_note: str | None = None) -> dict:
    """`mode`: nueva | editar | corregir."""
    areas = human_capital_catalog_repository.catalog_values("area_consultora")
    seniorities = human_capital_catalog_repository.catalog_values("seniority")
    saved_seniority = list(values.get("seniority") or [])
    legacy_seniority = [s for s in saved_seniority if s not in seniorities]
    modalidades_contrato = human_capital_catalog_repository.catalog_values("modalidad_contratacion")
    return _base_context(
        request, user,
        mode=mode, values=values, submission_id=submission_id, view=view, error=error, warning=warning, status_note=status_note,
        areas=areas, area_is_legacy=_legacy_option(values.get("area_consultora"), areas),
        seniorities=seniorities + legacy_seniority, legacy_seniority=legacy_seniority,
        catalog_areas_missing=not areas, catalog_seniority_missing=not seniorities,
        tipos_busqueda=human_capital_service.TIPOS_BUSQUEDA, tipo_labels=TIPO_LABELS,
        dedicacion_unidades=tuple(human_capital_service.DEDICACION_UNIDADES), dedicacion_labels=DEDICACION_LABELS,
        duracion_tipos=human_capital_service.DURACION_TIPOS, duracion_labels=DURACION_LABELS,
        duracion_meses_max=human_capital_service.DURACION_MESES_MAX, cantidad_max=human_capital_service.CANTIDAD_RECURSOS_MAX,
        dias_hibrido_max=human_capital_service.DIAS_PRESENCIALES_HIBRIDO_MAX,
        modalidades=busqueda_repository.MODALIDADES, ingles_niveles=human_capital_service.INGLES_NIVELES,
        modalidades_contrato=modalidades_contrato, contrato_is_legacy=_legacy_option(values.get("tipo_contrato"), modalidades_contrato),
        catalog_contrato_missing=not modalidades_contrato,
    )


def _render_form(request: Request, user: dict, status_code: int = 200, **kwargs) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="operations/busqueda_form.html", context=_form_context(request, user, **kwargs), status_code=status_code)


async def _read_form(request: Request) -> dict:
    """Campos del formulario REG-CH-01 como dict (seniority multi-valor). Los campos desconocidos se ignoran (lista blanca)."""
    form = await request.form()
    values = {k: form.get(k) for k in _FORM_KEYS}
    values = {k: (v if isinstance(v, str) else None) for k, v in values.items()}
    values["seniority"] = [v for v in form.getlist("seniority") if isinstance(v, str)]
    return values


def _parse_revision(raw) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(400, "expected_revision inválido") from None


def _solicitud_or_404(busqueda_id: str) -> dict:
    """Vista segura de la solicitud o 404 (también para búsquedas que no son solicitudes de Operaciones y para ids inválidos)."""
    try:
        view = human_capital_service.get_solicitud_view(busqueda_id)
    except _STORAGE_ERRORS:
        raise HTTPException(503, _MSG_SOURCE) from None
    if view is None:
        raise HTTPException(404, "Solicitud no encontrada")
    return view


# ---- Listado --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def _list_context(estado: str, q: str, page, page_size) -> dict:
    estado = estado if estado in INDICADORES else ""
    q = " ".join((q or "").split())[:120]
    source_error = None
    rows: list[dict] = []
    try:
        rows = human_capital_service.list_solicitudes_view()
    except _STORAGE_ERRORS:
        source_error = _MSG_SOURCE
    for r in rows:
        r["indicador"] = indicador_of(r)
    needle = _fold(q)
    visible = [
        r for r in rows                                                       # filtros + búsqueda ANTES de paginar
        if (not estado or r["indicador"] == estado)
        and (not needle or needle in _fold(r["requerimiento"].get("nombre_puesto")) or needle in _fold(r["requerimiento"].get("proyecto")))
    ]
    # Orden: fecha de inicio prevista DESCENDENTE (las sin fecha al final), desempate por apertura y luego por id; ANTES de paginar.
    visible.sort(key=lambda r: (r["requerimiento"].get("fecha_inicio_prevista") or "", r.get("fecha_apertura") or "", r["busqueda_id"]), reverse=True)
    pager = pagination.paginate(visible, page=page, page_size=page_size, base_url=BASE_PATH, params={"estado": estado, "q": q})
    return {
        "rows": pager["items"], "total_rows": pager["total"], "pager": pager,     # total FILTRADO (contador del dashboard-section), nunca el tamaño de la página
        "has_any": bool(rows), "filtro_estado": estado, "q": q, "estado_options": FILTRO_ESTADOS, "new_url": f"{BASE_PATH}/nueva",   # destino único de «+ Nueva búsqueda» (botón superior y estado vacío)
        "indicadores": INDICADORES, "badge_tonos": BADGE_TONOS, "tipo_labels": TIPO_LABELS, "source_error": source_error,
    }


@router.get(BASE_PATH, response_class=HTMLResponse, include_in_schema=False)
def operations_busquedas_list(request: Request, estado: str = Query(""), q: str = Query(""), ok: str = Query(""),
                              page: str = Query(""), page_size: str = Query("")):
    user, redirect = _authorize_read(request)
    if redirect is not None:
        return redirect
    ctx = _list_context(estado, q, page, page_size)
    # Filtros/paginación reactivos (HTMX): solo se re-renderiza el panel (`#opsb-panel`), MISMO criterio de fragmento que las listas de CCHH.
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request=request, name="partials/operations_busquedas_panel.html", context={"request": request, **ctx})
    return templates.TemplateResponse(request=request, name="operations/busquedas.html", context=_base_context(request, user, ok_message=_OK_MESSAGES.get(ok), **ctx))


# ---- Alta -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@router.get(f"{BASE_PATH}/nueva", response_class=HTMLResponse, include_in_schema=False)
def operations_busqueda_new_page(request: Request):
    user, redirect = _authorize_read(request)
    if redirect is not None:
        return redirect
    # `submission_id` lo genera el SERVIDOR al renderizar y se reutiliza en cada reintento del mismo formulario (idempotencia del envío).
    return _render_form(request, user, mode="nueva", values={"seniority": []}, submission_id=str(uuid.uuid4()))


@router.post(f"{BASE_PATH}/nueva", response_class=HTMLResponse, include_in_schema=False)
async def operations_busqueda_new_submit(request: Request):
    user, actor = _authorize_write(request)
    form = await request.form()
    submission_id = form.get("submission_id") if isinstance(form.get("submission_id"), str) else ""
    values = await _read_form(request)

    def _again(message: str, status_code: int, *, fresh_id: bool = False, warning: str | None = None):
        return _render_form(request, user, status_code, mode="nueva", values=values,
                            submission_id=str(uuid.uuid4()) if fresh_id else submission_id, error=message if warning is None else None, warning=warning)

    try:
        busqueda_repository.normalize_submission_id(submission_id)
    except ValueError:
        return _again("El formulario venció o es inválido. Revisá los datos y volvé a enviarlos.", 422, fresh_id=True)
    try:
        view = human_capital_service.create_solicitud(submission_id, values, actor)
    except busqueda_repository.BusquedaSubmissionConflictError:
        return _again("", 409, fresh_id=True, warning="Este envío ya había sido registrado con otro contenido. Revisá «Búsquedas» antes de reenviar: "
                                                     "lo que enviás ahora se registra como una solicitud nueva.")
    except busqueda_repository.BusquedaInvalidTransitionError:
        return _again(_MSG_CONCURRENT, 409)
    except busqueda_repository.BusquedaRemoteOutcomeUnknownError:
        return _again(_MSG_UNKNOWN_OUTCOME, 503)
    except _STORAGE_ERRORS:
        return _again(_MSG_STORAGE, 503)
    except ValueError as exc:
        return _again(str(exc), 422)
    return RedirectResponse(url=f"{BASE_PATH}/{view['busqueda_id']}?ok=enviada", status_code=303)


# ---- Detalle --------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@router.get(BASE_PATH + "/{busqueda_id}", response_class=HTMLResponse, include_in_schema=False)
def operations_busqueda_detail(request: Request, busqueda_id: str, ok: str = Query("")):
    user, redirect = _authorize_read(request)
    if redirect is not None:
        return redirect
    view = _solicitud_or_404(busqueda_id)
    return _render_detail(request, user, view, ok_message=_OK_MESSAGES.get(ok))


def _render_detail(request: Request, user: dict, view: dict, status_code: int = 200, **extra) -> HTMLResponse:
    view = dict(view)
    view["indicador"] = indicador_of(view)
    return templates.TemplateResponse(
        request=request, name="operations/busqueda_detail.html",
        context=_base_context(
            request, user, view=view, indicadores=INDICADORES, badge_tonos=BADGE_TONOS, veredicto_tonos=VEREDICTO_TONOS, tipo_labels=TIPO_LABELS, dedicacion_labels=DEDICACION_LABELS,
            transicion_labels=TRANSICION_LABELS, **extra),
        status_code=status_code,
    )


# ---- Edición (enviada) / corrección y reenvío (observada) ------------------------------------------------------------------------------------------------
@router.get(BASE_PATH + "/{busqueda_id}/editar", response_class=HTMLResponse, include_in_schema=False)
def operations_busqueda_edit_page(request: Request, busqueda_id: str):
    user, redirect = _authorize_read(request)
    if redirect is not None:
        return redirect
    view = _solicitud_or_404(busqueda_id)
    if not (view["puede_editar"] or view["puede_corregir"]):
        return RedirectResponse(url=f"{BASE_PATH}/{busqueda_id}", status_code=303)   # en gestión / cancelada: solo lectura
    return _render_form(request, user, mode="corregir" if view["puede_corregir"] else "editar", values=_form_values_from_view(view), view=view)


@router.post(BASE_PATH + "/{busqueda_id}/editar", response_class=HTMLResponse, include_in_schema=False)
async def operations_busqueda_edit_submit(request: Request, busqueda_id: str):
    user, actor = _authorize_write(request)
    view = _solicitud_or_404(busqueda_id)
    form = await request.form()
    revision = _parse_revision(form.get("expected_revision"))
    values = await _read_form(request)
    if not (view["puede_editar"] or view["puede_corregir"]):
        return _render_detail(request, user, view, 409, error=_MSG_CONCURRENT)
    mode = "corregir" if view["puede_corregir"] else "editar"

    def _again(message: str, status_code: int):
        return _render_form(request, user, status_code, mode=mode, values=values, view=view, error=message)

    try:
        if mode == "corregir":
            human_capital_service.resubmit_solicitud(busqueda_id, revision, values, actor)
            ok = "reenviada"
        else:
            human_capital_service.edit_solicitud(busqueda_id, revision, values, actor)
            ok = "editada"
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Solicitud no encontrada") from None
    except busqueda_repository.BusquedaConcurrentModificationError:
        return _again(_MSG_CONCURRENT, 409)
    except (busqueda_repository.BusquedaInvalidTransitionError, busqueda_repository.BusquedaSubmissionConflictError):
        return _again(_MSG_CONCURRENT, 409)
    except busqueda_repository.BusquedaRemoteOutcomeUnknownError:
        return _again(_MSG_UNKNOWN_OUTCOME, 503)
    except _STORAGE_ERRORS:
        return _again(_MSG_STORAGE, 503)
    except ValueError as exc:
        return _again(str(exc), 422)
    return RedirectResponse(url=f"{BASE_PATH}/{busqueda_id}?ok={ok}", status_code=303)


# ---- Cancelación -----------------------------------------------------------------------------------------------------------------------------------------------------------------
# ---- Eliminación (solo antes de que CCHH la tome) ----------------------------------------------------------------------------------------------------------
@router.post(BASE_PATH + "/{busqueda_id}/eliminar", response_class=HTMLResponse, include_in_schema=False)
async def operations_busqueda_delete(request: Request, busqueda_id: str):
    user, actor = _authorize_write(request)
    view = _solicitud_or_404(busqueda_id)
    form = await request.form()
    revision = _parse_revision(form.get("expected_revision"))
    try:
        human_capital_service.delete_solicitud(busqueda_id, revision, actor)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Solicitud no encontrada") from None
    except busqueda_repository.BusquedaConcurrentModificationError:
        return _render_detail(request, user, _solicitud_or_404(busqueda_id), 409, error=_MSG_CONCURRENT)
    except busqueda_repository.BusquedaInvalidTransitionError:
        return _render_detail(request, user, _solicitud_or_404(busqueda_id), 409, error=_MSG_NOT_DELETABLE)
    except busqueda_repository.BusquedaRemoteOutcomeUnknownError:
        return _render_detail(request, user, view, 503, error=_MSG_UNKNOWN_OUTCOME)
    except _STORAGE_ERRORS:
        return _render_detail(request, user, view, 503, error=_MSG_STORAGE)
    except ValueError as exc:
        return _render_detail(request, user, view, 422, error=str(exc))
    return RedirectResponse(url=f"{BASE_PATH}?ok=eliminada", status_code=303)


@router.post(BASE_PATH + "/{busqueda_id}/cancelar", response_class=HTMLResponse, include_in_schema=False)
async def operations_busqueda_cancel(request: Request, busqueda_id: str):
    user, actor = _authorize_write(request)
    view = _solicitud_or_404(busqueda_id)
    form = await request.form()
    revision = _parse_revision(form.get("expected_revision"))
    motivo = form.get("motivo") if isinstance(form.get("motivo"), str) else None
    try:
        human_capital_service.cancel_solicitud(busqueda_id, revision, actor, motivo)
    except busqueda_repository.BusquedaNotFoundError:
        raise HTTPException(404, "Solicitud no encontrada") from None
    except (busqueda_repository.BusquedaConcurrentModificationError, busqueda_repository.BusquedaInvalidTransitionError):
        return _render_detail(request, user, view, 409, error=_MSG_CONCURRENT)
    except busqueda_repository.BusquedaRemoteOutcomeUnknownError:
        return _render_detail(request, user, view, 503, error=_MSG_UNKNOWN_OUTCOME)
    except _STORAGE_ERRORS:
        return _render_detail(request, user, view, 503, error=_MSG_STORAGE)
    except ValueError as exc:
        return _render_detail(request, user, view, 422, error=str(exc))
    return RedirectResponse(url=f"{BASE_PATH}/{busqueda_id}?ok=cancelada", status_code=303)
