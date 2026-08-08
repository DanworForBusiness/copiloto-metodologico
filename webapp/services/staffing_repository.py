"""
staffing_repository.py — Acceso a datos de Staffing en SharePoint (sitio
"Gestión"). Fase de aceleración post-Fase 2 (Batch 2).

Sabe DÓNDE viven las tres fuentes del primer slice de Staffing
(Asignaciones por proyecto / Proyectos / Colaboradores), cómo construir las
URLs de Graph, cómo pedirlas vía `core.microsoft365` y cómo unir las tres
listas por sus IDs de lookup de SharePoint. Devuelve una representación
normalizada y estable (`StaffingAllocationRecord`) — nunca el JSON crudo de
Graph (`{"id": ..., "fields": {...}}`) más allá de este módulo.

Clasificación: OPERATIONS / STAFFING DATA ACCESS. NOT PLATFORM / SHARED. NO
importa `webapp.main`, `fastapi`, templates ni nada de autenticación/sesión.
Cada operación de Graph recibe `access_token` explícito — este módulo NO lo
posee, no lo persiste, no lo loguea.

⚠️ MAPEO DE CAMPOS SIN VALIDAR: los internal names de columna Graph expone
(`fields.<InternalName>`) NO son el label en español que ve el usuario, y
ese internal name no está confirmado en ningún lugar del repo/HANDOFF para
estas 3 listas (verificado explícitamente antes de este batch — a
diferencia de `core/sharepoint_cases.py`, donde "Title"/"Historia"/
"NumerodeCaso" sí son internal names reales ya probados contra ese sitio).
`_ASIGNACION_FIELDS` abajo son un mapeo PROVISORIO — mismo estilo de
nombrado observado en `sharepoint_cases.py` (sin espacios/acentos), pero NO
confirmado contra el sitio Gestión real. `_TITLE_FIELD = "Title"` es la
única excepción con base real: es la convención casi universal de SharePoint
para la columna principal de cualquier lista, salvo que la hayan
personalizado. Corregir `_ASIGNACION_FIELDS` es el prerequisito de
validación pendiente antes de que Batch 3 muestre datos reales (ver
HANDOFF, Batch 2).
"""
from __future__ import annotations

from dataclasses import dataclass

import config
from core import microsoft365

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Nombre de columna estándar de SharePoint para el título/nombre principal de
# cualquier lista (Proyectos, Colaboradores) — convención real, no adivinada.
_TITLE_FIELD = "Title"

# ⚠️ Sin validar contra el sitio Gestión real (ver docstring del módulo).
_ASIGNACION_FIELDS = {
    "consultor_lookup_id": "ColaboradorLookupId",
    "proyecto_lookup_id": "ProyectoLookupId",
    "allocation": "Asignacion",
    "status": "Estado",
    "start_date": "FechaInicio",
    "end_date": "FechaFin",
}


@dataclass(frozen=True)
class StaffingAllocationRecord:
    """Representación normalizada y estable de una fila de Asignaciones por
    proyecto, ya resuelta contra Proyectos/Colaboradores. `allocation_unit`
    queda "unknown" hasta que se confirme si el valor fuente es horas,
    porcentaje u otra unidad — ver Batch 2 en HANDOFF."""

    assignment_id: str
    consultant_id: str | None
    consultant_name: str | None
    project_id: str | None
    project_name: str | None
    allocation_value: float | None
    allocation_unit: str
    status: str | None
    start_date: str | None
    end_date: str | None


def _list_items_url(site_id: str, list_id: str) -> str:
    return f"{GRAPH_BASE}/sites/{site_id}/lists/{list_id}/items?$expand=fields"


def _stringify(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolved_title(item: dict | None) -> str | None:
    if not item:
        return None
    fields = item.get("fields") or {}
    return _stringify(fields.get(_TITLE_FIELD))


class StaffingRepository:
    """Lee las 3 listas de Staffing del sitio Gestión y devuelve
    `StaffingAllocationRecord`s ya unidos. Sin estado mutable de proceso:
    cada llamada a `get_allocations` es independiente, `access_token` viaja
    explícito por parámetro."""

    def __init__(
        self,
        *,
        site_id: str | None = None,
        asignaciones_list_id: str | None = None,
        proyectos_list_id: str | None = None,
        colaboradores_list_id: str | None = None,
    ):
        self.site_id = site_id if site_id is not None else config.SHAREPOINT_GESTION_SITE_ID
        self.asignaciones_list_id = asignaciones_list_id or config.SHAREPOINT_ASIGNACIONES_LIST_ID
        self.proyectos_list_id = proyectos_list_id or config.SHAREPOINT_PROYECTOS_LIST_ID
        self.colaboradores_list_id = colaboradores_list_id or config.SHAREPOINT_COLABORADORES_LIST_ID

    def _require_site_id(self) -> str:
        if not self.site_id:
            raise RuntimeError(
                "SHAREPOINT_GESTION_SITE_ID no está configurado. Es un prerequisito "
                "de despliegue pendiente (Batch 2, ver HANDOFF) antes de que Staffing "
                "pueda leer el sitio Gestión — no se degrada a una lista vacía a "
                "propósito, para no esconder un dato de negocio real detrás de un "
                "'sin resultados'."
            )
        return self.site_id

    def get_allocations(self, access_token: str) -> list[StaffingAllocationRecord]:
        site_id = self._require_site_id()

        asignaciones = microsoft365.graph_get_paged_values(
            _list_items_url(site_id, self.asignaciones_list_id), access_token,
        )
        proyectos = microsoft365.graph_get_paged_values(
            _list_items_url(site_id, self.proyectos_list_id), access_token,
        )
        colaboradores = microsoft365.graph_get_paged_values(
            _list_items_url(site_id, self.colaboradores_list_id), access_token,
        )

        proyectos_by_id = {str(item.get("id")): item for item in proyectos if item.get("id") is not None}
        colaboradores_by_id = {str(item.get("id")): item for item in colaboradores if item.get("id") is not None}

        records: list[StaffingAllocationRecord] = []
        for item in asignaciones:
            fields = item.get("fields") or {}
            consultor_id = _stringify(fields.get(_ASIGNACION_FIELDS["consultor_lookup_id"]))
            proyecto_id = _stringify(fields.get(_ASIGNACION_FIELDS["proyecto_lookup_id"]))
            # Degradación determinística (sección 8): una referencia de lookup
            # sin resolver NO descarta la fila — el id crudo se conserva, el
            # nombre resuelto queda en None (el consumidor decide cómo
            # mostrarlo, p. ej. "(sin resolver)").
            consultor_item = colaboradores_by_id.get(consultor_id) if consultor_id else None
            proyecto_item = proyectos_by_id.get(proyecto_id) if proyecto_id else None
            records.append(
                StaffingAllocationRecord(
                    assignment_id=str(item.get("id") or ""),
                    consultant_id=consultor_id,
                    consultant_name=_resolved_title(consultor_item),
                    project_id=proyecto_id,
                    project_name=_resolved_title(proyecto_item),
                    allocation_value=_to_float(fields.get(_ASIGNACION_FIELDS["allocation"])),
                    allocation_unit="unknown",
                    status=_stringify(fields.get(_ASIGNACION_FIELDS["status"])),
                    start_date=_stringify(fields.get(_ASIGNACION_FIELDS["start_date"])),
                    end_date=_stringify(fields.get(_ASIGNACION_FIELDS["end_date"])),
                )
            )
        return records
