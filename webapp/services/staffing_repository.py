"""
staffing_repository.py — Acceso a datos de Staffing en SharePoint (sitio
"Gestión"). Fase de aceleración post-Fase 2 (Batch 2, con corrective pass).

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

⚠️ MAPEO DE CAMPOS — SIN DEFAULT DE PRODUCCIÓN: los internal names de
columna que Graph expone (`fields.<InternalName>`) NO son el label en
español que ve el usuario, y ninguno de los 8 nombres que necesita este
repositorio (6 de Asignaciones + el nombre de Proyecto + el de Colaborador)
está confirmado en ningún lugar del repo/HANDOFF. La primera versión de
este módulo usaba nombres inferidos por convención (`ColaboradorLookupId`,
`Asignacion`, `Title` para ambos nombres, etc.) como default — un
corrective pass los removió: ni siquiera `Title` se asume, porque que
SharePoint tenga una columna `Title` estándar NO prueba que Proyectos o
Colaboradores la usen como el nombre que Staffing debe mostrar. Ahora el
mapeo es un objeto explícito (`StaffingFieldMapping`) sin ningún default —
`StaffingFieldMapping.from_config()` lo construye desde `config.py` (vacío
si no está configurado; construirlo NO falla, así el import de la app no se
rompe por Staffing sin configurar), y `StaffingRepository.get_allocations`
falla explícito, listando exactamente qué falta, si se intenta leer sin
mapeo completo. Los tests inyectan su propio mapeo fixture (ver
`tests/webapp/test_staffing_repository.py`) — determinístico, pero
explícitamente NO una prueba de los nombres reales.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields

import config
from core import microsoft365

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass(frozen=True)
class StaffingFieldMapping:
    """Internal names de columna Graph para las 3 listas de Staffing. NINGÚN
    campo tiene default — deben proveerse explícitos: en producción,
    confirmados contra el sitio Gestión real (`from_config()`, hoy vacío);
    en tests, por un fixture documentado como tal. Construir esta clase con
    valores vacíos NO falla (permite `from_config()` sin romper el import
    de la app); lo que falla es `StaffingRepository.get_allocations` si
    intenta usarla incompleta."""

    colaborador_lookup_id: str
    proyecto_lookup_id: str
    allocation: str
    status: str
    start_date: str
    end_date: str
    proyecto_title: str
    colaborador_title: str

    @classmethod
    def from_config(cls) -> "StaffingFieldMapping":
        return cls(
            colaborador_lookup_id=config.SHAREPOINT_ASIGNACIONES_FIELD_COLABORADOR_LOOKUP,
            proyecto_lookup_id=config.SHAREPOINT_ASIGNACIONES_FIELD_PROYECTO_LOOKUP,
            allocation=config.SHAREPOINT_ASIGNACIONES_FIELD_ALLOCATION,
            status=config.SHAREPOINT_ASIGNACIONES_FIELD_STATUS,
            start_date=config.SHAREPOINT_ASIGNACIONES_FIELD_START_DATE,
            end_date=config.SHAREPOINT_ASIGNACIONES_FIELD_END_DATE,
            proyecto_title=config.SHAREPOINT_PROYECTOS_FIELD_TITLE,
            colaborador_title=config.SHAREPOINT_COLABORADORES_FIELD_TITLE,
        )

    def missing_fields(self) -> list[str]:
        return [f.name for f in dataclass_fields(self) if not getattr(self, f.name)]


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


def _resolved_title(item: dict | None, title_field: str) -> str | None:
    if not item:
        return None
    fields = item.get("fields") or {}
    return _stringify(fields.get(title_field))


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
        field_mapping: StaffingFieldMapping | None = None,
    ):
        self.site_id = site_id if site_id is not None else config.SHAREPOINT_GESTION_SITE_ID
        self.asignaciones_list_id = asignaciones_list_id or config.SHAREPOINT_ASIGNACIONES_LIST_ID
        self.proyectos_list_id = proyectos_list_id or config.SHAREPOINT_PROYECTOS_LIST_ID
        self.colaboradores_list_id = colaboradores_list_id or config.SHAREPOINT_COLABORADORES_LIST_ID
        self.field_mapping = field_mapping if field_mapping is not None else StaffingFieldMapping.from_config()

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

    def _require_field_mapping(self) -> StaffingFieldMapping:
        missing = self.field_mapping.missing_fields()
        if missing:
            raise RuntimeError(
                "Falta configurar el mapeo de campos de Staffing (Batch 2, ver "
                "HANDOFF) — ninguno tiene default de producción a propósito, para "
                "no leer un campo equivocado sin que nadie lo note. Faltan: "
                + ", ".join(missing)
            )
        return self.field_mapping

    def get_allocations(self, access_token: str) -> list[StaffingAllocationRecord]:
        site_id = self._require_site_id()
        mapping = self._require_field_mapping()

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
            consultor_id = _stringify(fields.get(mapping.colaborador_lookup_id))
            proyecto_id = _stringify(fields.get(mapping.proyecto_lookup_id))
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
                    consultant_name=_resolved_title(consultor_item, mapping.colaborador_title),
                    project_id=proyecto_id,
                    project_name=_resolved_title(proyecto_item, mapping.proyecto_title),
                    allocation_value=_to_float(fields.get(mapping.allocation)),
                    allocation_unit="unknown",
                    status=_stringify(fields.get(mapping.status)),
                    start_date=_stringify(fields.get(mapping.start_date)),
                    end_date=_stringify(fields.get(mapping.end_date)),
                )
            )
        return records
