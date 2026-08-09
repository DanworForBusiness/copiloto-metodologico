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
_SIN_DATO = "Sin dato"

# Mismo estilo de abreviatura ya usado en whats_new_service.py — no se
# centraliza en un util compartido para un solo consumidor más.
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# Horizonte del timeline (Asignaciones Dashboard v1): mes anterior + mes
# actual + 6 meses siguientes = 8 columnas. Deliberadamente mensual, no
# diario/semanal — coincide con la unidad real del dato (`Horaspormes`).
_HORIZON_MONTHS_BEFORE = 1
_HORIZON_MONTHS_AFTER = 6
_HORIZON_SIZE = _HORIZON_MONTHS_BEFORE + 1 + _HORIZON_MONTHS_AFTER


def _add_months(d: date, delta: int) -> date:
    total = d.year * 12 + (d.month - 1) + delta
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, 1)


def compute_horizon_months(today: date | None = None) -> list[date]:
    """Horizonte rolling determinístico: mes anterior → mes actual → 6
    meses siguientes. `today` explícito para tests deterministas; sin él,
    `date.today()` (uso real del router)."""
    anchor = (today or date.today()).replace(day=1)
    start = _add_months(anchor, -_HORIZON_MONTHS_BEFORE)
    return [_add_months(start, i) for i in range(_HORIZON_SIZE)]


def _month_label(d: date) -> str:
    return f"{_MESES[d.month - 1]} {d.year}"


def _month_key(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _month_index_clamped(month_start: date, horizon_months: list[date]) -> int:
    key = _month_key(month_start)
    keys = [_month_key(m) for m in horizon_months]
    if key <= keys[0]:
        return 0
    if key >= keys[-1]:
        return len(keys) - 1
    return keys.index(key)


def compute_timeline_position(start: date | None, end: date | None, horizon_months: list[date]) -> dict:
    """Posición pura de una asignación en el horizonte del timeline —
    índices de mes (0..len-1), NUNCA aritmética de píxeles. Sin fechas
    reales (ambas `None`), la fila queda fuera del timeline (`on_timeline`
    False) — sigue existiendo en KPIs/detalle, solo no se puede posicionar.
    Con un solo extremo conocido, el otro se trata como abierto: sin inicio
    → arranca clippeado al primer mes visible; sin fin → se extiende
    clippeada hasta el último mes visible (asignación en curso sin fecha de
    cierre conocida). Las fechas reales NUNCA se alteran — esto solo decide
    dónde se dibuja la barra."""
    if start is None and end is None:
        return {"on_timeline": False, "start_index": 0, "end_index": 0, "clipped_start": False, "clipped_end": False}

    horizon_first_key = _month_key(horizon_months[0])
    horizon_last_key = _month_key(horizon_months[-1])

    if start is None:
        start_index = 0
        clipped_start = True
    else:
        start_month = date(start.year, start.month, 1)
        clipped_start = _month_key(start_month) < horizon_first_key
        start_index = _month_index_clamped(start_month, horizon_months)

    if end is None:
        end_index = len(horizon_months) - 1
        clipped_end = True
    else:
        end_month = date(end.year, end.month, 1)
        clipped_end = _month_key(end_month) > horizon_last_key
        end_index = _month_index_clamped(end_month, horizon_months)

    if end_index < start_index:
        end_index = start_index

    return {
        "on_timeline": True,
        "start_index": start_index,
        "end_index": end_index,
        "clipped_start": clipped_start,
        "clipped_end": clipped_end,
    }


@dataclass(frozen=True)
class StaffingOverviewRow:
    """Fila lista para el primer read-model de Staffing (Batch 3 la vuelca a
    HTML sin más transformación de negocio)."""

    assignment_id: str
    consultant_id: str | None
    consultant_name: str
    project_id: str | None
    project_name: str
    allocation_value: float | None
    allocation_unit: str
    capacity_percentage: float | None
    status: str
    start_date: date | None
    end_date: date | None
    # Enriquecimiento OPCIONAL (Asignaciones Dashboard v1) — passthrough
    # puro del valor real de Colaboradores (Full Time/Part Time). `None` si
    # el mapeo no está configurado o el valor fuente viene vacío; NUNCA se
    # deriva una regla de capacidad a partir de este campo.
    consultant_availability: str | None = None


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
            # IDs estables del lookup crudo — sobreviven aunque el nombre no
            # resuelva (ver docstring de StaffingAllocationRecord). El
            # dashboard cuenta consultores/proyectos DISTINTOS por id, nunca
            # por nombre (dos personas podrían compartir display name).
            consultant_id=record.consultant_id,
            consultant_name=record.consultant_name or _UNRESOLVED_LABEL,
            project_id=record.project_id,
            project_name=record.project_name or _UNRESOLVED_LABEL,
            allocation_value=record.allocation_value,
            allocation_unit=record.allocation_unit,
            capacity_percentage=self._capacity_percentage(record),
            status=(record.status or "").strip(),
            start_date=_parse_date(record.start_date),
            end_date=_parse_date(record.end_date),
            # Passthrough puro — sin fallback a "Sin dato" acá; esa es una
            # decisión de PRESENTACIÓN (ver build_dashboard_payload), igual
            # que `status` se guarda "crudo" y el template decide el "—".
            consultant_availability=record.consultant_availability,
        )

    @staticmethod
    def _capacity_percentage(record: StaffingAllocationRecord) -> float | None:
        # allocation_unit ya es "hours_per_month" (validado, Batch 3) — pero
        # la semántica de CAPACIDAD (baseline FT/PT, over-allocation, etc.)
        # es el próximo incremento funcional, no este batch. Deliberadamente
        # None, no una cuenta falsa (ver docstring del módulo).
        return None


def _allocation_label(row: StaffingOverviewRow) -> str:
    if row.allocation_value is None:
        return "—"
    if row.allocation_unit == "hours_per_month":
        return f"{row.allocation_value:g} h/mes"
    return f"{row.allocation_value} ({row.allocation_unit})"


def build_dashboard_payload(rows: list[StaffingOverviewRow], *, today: date | None = None) -> dict:
    """Transformación PURA (sin Graph/repositorio) de `StaffingOverviewRow`s
    al payload JSON-serializable que consume el dashboard client-side
    (Alpine, ver `staffingDashboard()` en app.js). Una sola fuente
    normalizada (`assignments`) — el filtrado posterior (KPIs/timeline/
    disponibilidad/detalle) es 100% client-side sobre este mismo array, sin
    volver a golpear Graph.

    Función LIBRE (no método de `StaffingService`) a propósito: no necesita
    `self` (es pura), y el router la importa directo — `StaffingService` en
    el router se reemplaza por un test double en varios tests existentes;
    atarla a la clase la haría depender de ese double implementar este
    método también."""
    horizon_months = compute_horizon_months(today)
    today_key = _month_key((today or date.today()).replace(day=1))
    horizon = [
        {"index": i, "label": _month_label(m), "is_current": _month_key(m) == today_key}
        for i, m in enumerate(horizon_months)
    ]
    assignments = []
    for row in rows:
        position = compute_timeline_position(row.start_date, row.end_date, horizon_months)
        assignments.append({
            "assignment_id": row.assignment_id,
            "consultant_id": row.consultant_id,
            "consultant_name": row.consultant_name,
            "consultant_unresolved": row.consultant_name == _UNRESOLVED_LABEL,
            "project_id": row.project_id,
            "project_name": row.project_name,
            "project_unresolved": row.project_name == _UNRESOLVED_LABEL,
            "allocation_value": row.allocation_value,
            "allocation_label": _allocation_label(row),
            "allocation_unit": row.allocation_unit,
            "status": row.status,
            "start_date": row.start_date.isoformat() if row.start_date else None,
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "availability": row.consultant_availability or _SIN_DATO,
            **position,
        })
    return {"assignments": assignments, "horizon": horizon}
