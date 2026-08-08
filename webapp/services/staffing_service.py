"""
staffing_service.py — Read-model de Staffing para la primera página
read-only. Fase de aceleración post-Fase 2 (Batch 2/3).

Consume `StaffingAllocationRecord`s ya normalizados/unidos de
`staffing_repository.py` y les da el significado de negocio mínimo que
necesita el primer slice: fechas parseadas, ordenamiento estable, y un
fallback de display para nombres sin resolver. NO conoce URLs de Graph,
list IDs ni field names — esa frontera la posee el repositorio.

Clasificación: OPERATIONS / STAFFING DOMAIN / READ MODEL. NOT PLATFORM /
SHARED. NO importa `core.microsoft365` directo (aísla esa dependencia
detrás de `StaffingRepository`), NO importa FastAPI/Jinja, NO define rutas,
NO mantiene estado mutable de proceso.

`allocation_unit` es "hours_per_month" — validado contra Graph real en
Batch 3 (campo `Horaspormes`). **Aun así, `capacity_percentage` sigue
`None` a propósito**: conocer la unidad no resuelve la semántica de
negocio de capacidad (baseline FT/PT, over-allocation, etc.) — eso es el
PRÓXIMO incremento funcional, no este batch. `FT_MONTHLY_CAPACITY_HOURS_HISTORICAL`/
`KNOWN_HISTORICAL_STATUSES` siguen siendo referencia histórica (Power BI
legado) documentada pero NO aplicada — de hecho, los valores reales de
`Status` observados en Batch 3 (Inactivo/Activo/Tentativo) NO coinciden
literalmente con `KNOWN_HISTORICAL_STATUSES` (Active/Inactive/Tentative/
Vaca, en inglés) — `status` sigue siendo passthrough puro del valor fuente,
nunca se compara contra esa constante.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from webapp.services.staffing_repository import StaffingAllocationRecord, StaffingRepository

# Referencia histórica (Power BI legado) — NO CONFIRMADA contra la fuente
# actual. No se usa en ningún cálculo de este batch.
FT_MONTHLY_CAPACITY_HOURS_HISTORICAL = 160
KNOWN_HISTORICAL_STATUSES = {"Active", "Inactive", "Tentative", "Vaca"}

_UNRESOLVED_LABEL = "(sin resolver)"


@dataclass(frozen=True)
class StaffingOverviewRow:
    """Fila lista para el primer read-model de Staffing (Batch 3 la vuelca a
    HTML sin más transformación de negocio)."""

    assignment_id: str
    consultant_name: str
    project_name: str
    allocation_value: float | None
    allocation_unit: str
    capacity_percentage: float | None
    status: str
    start_date: date | None
    end_date: date | None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class StaffingService:
    """Repository injectable para tests (mock sin tocar `core.microsoft365`)."""

    def __init__(self, repository: StaffingRepository | None = None):
        self._repository = repository if repository is not None else StaffingRepository()

    def get_overview(self, access_token: str) -> list[StaffingOverviewRow]:
        records = self._repository.get_allocations(access_token)
        rows = [self._to_row(record) for record in records]
        # Orden estable por consultor luego proyecto — determinístico para
        # que el mismo dato fuente siempre rinda el mismo orden en pantalla.
        rows.sort(key=lambda row: (row.consultant_name, row.project_name))
        return rows

    def _to_row(self, record: StaffingAllocationRecord) -> StaffingOverviewRow:
        return StaffingOverviewRow(
            assignment_id=record.assignment_id,
            consultant_name=record.consultant_name or _UNRESOLVED_LABEL,
            project_name=record.project_name or _UNRESOLVED_LABEL,
            allocation_value=record.allocation_value,
            allocation_unit=record.allocation_unit,
            capacity_percentage=self._capacity_percentage(record),
            status=(record.status or "").strip(),
            start_date=_parse_date(record.start_date),
            end_date=_parse_date(record.end_date),
        )

    @staticmethod
    def _capacity_percentage(record: StaffingAllocationRecord) -> float | None:
        # allocation_unit ya es "hours_per_month" (validado, Batch 3) — pero
        # la semántica de CAPACIDAD (baseline FT/PT, over-allocation, etc.)
        # es el próximo incremento funcional, no este batch. Deliberadamente
        # None, no una cuenta falsa (ver docstring del módulo).
        return None
