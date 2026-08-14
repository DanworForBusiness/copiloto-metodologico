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

Staffing Director Priority V2 (ver HANDOFF): `get_overview` gana
`assignment_scope` (passthrough puro a `StaffingRepository`, ver ahí) y
`build_dashboard_payload` gana `is_activo_vencido` — anomalía analítica
derivada de Status+FechaFin, NUNCA una reescritura del Status crudo.

Staffing Approved Absence Overlay (ver HANDOFF — generalización del
Vacation Timeline Overlay original): overlay ADITIVO, presentacional —
NUNCA toca KPIs/capacidad/allocation de Asignaciones. `_normalize_
consultant_name` es el ÚNICO criterio de identidad para el join
Ausencias↔Colaboradores: trim + colapso de espacios + casefold + folding
determinístico de diacríticos (Unicode NFKD, sin heurística de idioma) —
SIN fuzzy-matching, sin reordenar nombres, sin nicknames. `join_absences_to_
consultants` es PURA (sin Graph): dado el universo de consultores conocido,
separa matched/unmatched/ambiguous — un nombre que normaliza igual que DOS
consultores distintos queda `ambiguous` (nunca se le asigna a ninguno), un
nombre que no matchea a nadie queda `unmatched` (nunca se descarta en
silencio, ver `AbsenceJoinResult`). `build_absence_overlay_payload` reusa
`compute_horizon_months`/`compute_timeline_position` — MISMA lógica de
posicionamiento que Asignaciones, sin una segunda implementación de
horizonte. Períodos que no se solapan con el horizonte visible se excluyen
(nunca se "clippean" al borde — un período íntegramente pasado/futuro fuera
de ventana no debe aparecer como si fuera vigente ahora). Cada período trae
`concept` YA CLASIFICADO server-side (`Vacaciones`/`Danwor Day`/
`Cumpleaños`/`Otros`, ver `staffing_repository._classify_absence_concept`)
— este módulo nunca ve ni reclasifica el Motivo crudo, solo transporta el
concepto ya resuelto hasta el payload."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from webapp.services.staffing_repository import (
    AbsencePeriod, StaffingAllocationRecord, StaffingRepository,
)

# Referencia histórica (Power BI legado) — NO CONFIRMADA contra la fuente
# actual. No se usa en ningún cálculo de este batch.
FT_MONTHLY_CAPACITY_HOURS_HISTORICAL = 160
KNOWN_HISTORICAL_STATUSES = {"Active", "Inactive", "Tentative", "Vaca"}

_UNRESOLVED_LABEL = "(sin resolver)"
_SIN_DATO = "Sin dato"

# Staffing Director Priority V2 (ver HANDOFF) — anomalía analítica "activo
# vencido": Status crudo == Activo pero FechaFin ya pasó. Helper ÚNICO y
# canónico — NUNCA reescribe el Status de SharePoint (la fuente sigue
# diciendo Activo), solo lo interpreta para KPI/detalle.
_ACTIVO_STATUS = "Activo"


def is_activo_vencido(status: str | None, end_date: date | None, *, today: date | None = None) -> bool:
    if (status or "").strip() != _ACTIVO_STATUS:
        return False
    if end_date is None:
        return False
    return end_date < (today or date.today())

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


def _date_position_fraction(d: date, horizon_months: list[date]) -> float:
    """Fracción día-exacta (0..100) de una fecha dentro del horizonte
    visible — cada mes ocupa 1/len(horizon_months) del ancho total,
    dividido linealmente por día dentro de ese mes. Único cálculo de
    coordenadas del timeline (ver HANDOFF — geometría día-exacta):
    `compute_timeline_position` y `compute_today_marker` son ambos
    envoltorios finos de este helper, así asignaciones, vacaciones y el
    marcador de "hoy" comparten un único modelo, nunca uno mensual y otro
    diario. Puede devolver un valor fuera de [0, 100] para una fecha fuera
    del horizonte — cada caller decide si clampear (`compute_timeline_
    position`) o descartar (`compute_today_marker`)."""
    total_months = len(horizon_months)
    month_start = date(d.year, d.month, 1)
    month_offset = (d.year - horizon_months[0].year) * 12 + (d.month - horizon_months[0].month)
    next_month_start = _add_months(month_start, 1)
    days_in_month = (next_month_start - month_start).days
    day_fraction = (d - month_start).days / days_in_month
    return (month_offset + day_fraction) / total_months * 100


def compute_timeline_position(start: date | None, end: date | None, horizon_months: list[date]) -> dict:
    """Posición día-exacta (PURA) de una asignación en el horizonte del
    timeline — `start_pct`/`end_pct` (0..100, MISMA escala que
    `compute_today_marker`), NUNCA índices de mes: una barra que arranca a
    mitad de mes se dibuja a mitad de esa columna, no pegada a su borde
    izquierdo (corrective día-exacta, ver HANDOFF — la geometría por índice
    de mes hacía que TODA asignación pareciera arrancar el día 1 de su
    mes). Sin fechas reales (ambas `None`), la fila queda fuera del
    timeline (`on_timeline` False) — sigue existiendo en KPIs/detalle, solo
    no se puede posicionar. Con un solo extremo conocido, el otro se trata
    como abierto: sin inicio → arranca clippeado al borde izquierdo visible
    (0%); sin fin → se extiende clippeada hasta el borde derecho visible
    (100%) (asignación en curso sin fecha de cierre conocida). `end_pct` se
    calcula sobre el INICIO del día SIGUIENTE a `end` (para que la barra
    cubra el día de fin completo, como en un Gantt) — nunca sobre `end` a
    las 0hs, que dejaría la barra un día corta. Las fechas reales NUNCA se
    alteran — esto solo decide dónde se dibuja la barra."""
    if start is None and end is None:
        return {"on_timeline": False, "start_pct": 0.0, "end_pct": 0.0, "clipped_start": False, "clipped_end": False}

    horizon_start = horizon_months[0]
    horizon_end_exclusive = _add_months(horizon_months[-1], 1)  # primer día YA fuera del horizonte

    if start is None:
        clipped_start = True
        start_pct = 0.0
    else:
        clipped_start = start < horizon_start
        effective_start = max(start, horizon_start)
        start_pct = _date_position_fraction(effective_start, horizon_months)

    if end is None:
        clipped_end = True
        end_pct = 100.0
    else:
        clipped_end = end >= horizon_end_exclusive
        effective_end_exclusive = min(end + timedelta(days=1), horizon_end_exclusive)
        end_pct = _date_position_fraction(effective_end_exclusive, horizon_months)

    if end_pct < start_pct:
        end_pct = start_pct

    return {
        "on_timeline": True,
        "start_pct": round(start_pct, 4),
        "end_pct": round(end_pct, 4),
        "clipped_start": clipped_start,
        "clipped_end": clipped_end,
    }


def compute_today_marker(today: date, horizon_months: list[date]) -> float | None:
    """Posición porcentual PURA (0..100) del día de hoy dentro del ancho
    total del horizonte — MISMO helper de coordenadas día-exacta que
    `compute_timeline_position` (`_date_position_fraction`), nunca una
    segunda implementación. `None` si hoy cae fuera del horizonte visible
    (no se dibuja marcador). El % es relativo SOLO al área de columnas de
    mes (el template lo posiciona dentro de un overlay que excluye la
    columna de etiqueta, así que este cálculo no necesita saber ningún
    ancho en píxeles)."""
    horizon_start = horizon_months[0]
    horizon_end_exclusive = _add_months(horizon_months[-1], 1)
    if today < horizon_start or today >= horizon_end_exclusive:
        return None
    return round(_date_position_fraction(today, horizon_months), 4)


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
    # Enriquecimiento OPCIONAL (revisión PBI, ver HANDOFF) — resuelto vía el
    # Proyecto (Proyecto.EmpresaLookupId → Cliente.Title), NUNCA vía el
    # nombre de proyecto. A diferencia de consultant_name/project_name, NO
    # tiene fallback a "(sin resolver)" — se queda `None` cuando no resuelve
    # (la presentación decide mostrarlo condicionalmente, nunca inventa una
    # identidad de cliente).
    client_id: str | None = None
    client_name: str | None = None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ============================================================================
# Ausencias — identidad, join, overlay (Staffing Approved Absence Overlay)
# ============================================================================

def _normalize_consultant_name(name: str | None) -> str:
    """Único criterio de identidad determinística para el join Vacaciones↔
    Colaboradores: trim + colapso de espacios repetidos + casefold + folding
    de diacríticos (NFKD, se descartan los combining marks — "Matías" y
    "Matias" normalizan igual). SIN fuzzy-matching: dos nombres que difieren
    en algo más que este folding NUNCA matchean."""
    collapsed = " ".join((name or "").split())
    decomposed = unicodedata.normalize("NFKD", collapsed.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@dataclass(frozen=True)
class AbsenceOverviewPeriod:
    """Un período de ausencia ya resuelto contra un consultor de Staffing
    ÚNICO e inequívoco (ver `join_absences_to_consultants`). `concept` y
    `hours` son passthrough puro de `AbsencePeriod` — el concepto YA viene
    clasificado del repositorio (nunca se reclasifica acá), `hours` es
    metadata de display (`Días × 8`), `None` si la fuente no lo trae; el
    join no interpreta ninguno de los dos."""

    source_id: str
    consultant_id: str
    consultant_name: str
    start_date: date
    end_date: date
    concept: str
    hours: int | None = None


@dataclass(frozen=True)
class AbsenceJoinResult:
    """Resultado del join determinístico. `unmatched`/`ambiguous` conservan
    los `AbsencePeriod` CRUDOS (nunca se descartan en silencio) — quedan
    disponibles para diagnóstico, nunca se les asigna un consultor por
    aproximación."""

    periods: list[AbsenceOverviewPeriod]
    unmatched: list[AbsencePeriod]
    ambiguous: list[AbsencePeriod]


def join_absences_to_consultants(
    absences: list[AbsencePeriod], known_consultants: list[tuple[str, str]],
) -> AbsenceJoinResult:
    """`known_consultants`: pares (consultant_id, consultant_name) del
    universo VIGENTE de Colaboradores de Staffing. Construye una key
    normalizada → lista de candidatos; una key con exactamente UN candidato
    matchea, cero candidatos es `unmatched`, más de uno es `ambiguous` (no se
    puede saber a cuál de los dos pertenece sin un ID compartido real — ver
    HANDOFF). Fechas inválidas/faltantes excluyen el período por completo
    (ni matched ni unmatched/ambiguous — no hay nada que overlayar sin un
    rango real)."""
    by_key: dict[str, list[tuple[str, str]]] = {}
    for consultant_id, consultant_name in known_consultants:
        by_key.setdefault(_normalize_consultant_name(consultant_name), []).append((consultant_id, consultant_name))

    periods: list[AbsenceOverviewPeriod] = []
    unmatched: list[AbsencePeriod] = []
    ambiguous: list[AbsencePeriod] = []
    for absence in absences:
        start = _parse_date(absence.start_date)
        end = _parse_date(absence.end_date)
        if start is None or end is None:
            continue
        candidates = by_key.get(_normalize_consultant_name(absence.consultant_name), [])
        if len(candidates) == 1:
            consultant_id, consultant_name = candidates[0]
            periods.append(AbsenceOverviewPeriod(
                source_id=absence.source_id, consultant_id=consultant_id,
                consultant_name=consultant_name, start_date=start, end_date=end,
                concept=absence.concept, hours=absence.hours,
            ))
        elif len(candidates) == 0:
            unmatched.append(absence)
        else:
            ambiguous.append(absence)
    return AbsenceJoinResult(periods=periods, unmatched=unmatched, ambiguous=ambiguous)


def _overlaps_horizon(start: date, end: date, horizon_months: list[date]) -> bool:
    """A diferencia de `compute_timeline_position` (que SIEMPRE posiciona una
    asignación, clippeada al borde si hace falta — correcto para
    asignaciones potencialmente abiertas), un período de vacaciones es
    FINITO y conocido en ambos extremos: si no se solapa en absoluto con el
    horizonte visible, no debe aparecer clippeado en el borde (eso
    sugeriría, falsamente, que está vigente ahora)."""
    horizon_start = horizon_months[0]
    horizon_end_exclusive = _add_months(horizon_months[-1], 1)
    return end >= horizon_start and start < horizon_end_exclusive


def build_absence_overlay_payload(join_result: AbsenceJoinResult, *, today: date | None = None) -> dict:
    """Transformación PURA del resultado del join al payload JSON del
    overlay — reusa `compute_horizon_months`/`compute_timeline_position`
    (MISMA ventana/posicionamiento día-exacta que `build_dashboard_payload`
    y el marcador de "hoy", nunca una segunda implementación de horizonte ni
    de geometría). `unmatched_count`/`ambiguous_count` son conteos puros
    para diagnóstico — nunca nombres/PII en el payload de vuelta al
    cliente. `concept` es el ÚNICO campo derivado de Motivo — el valor crudo
    nunca existió más allá del repositorio (ver `AbsencePeriod`), así que no
    hay nada que filtrar acá: el payload ya es privacy-safe por
    construcción."""
    horizon_months = compute_horizon_months(today or date.today())
    absences = []
    for period in join_result.periods:
        if not _overlaps_horizon(period.start_date, period.end_date, horizon_months):
            continue
        position = compute_timeline_position(period.start_date, period.end_date, horizon_months)
        absences.append({
            "source_id": period.source_id,
            "consultant_id": period.consultant_id,
            "consultant_name": period.consultant_name,
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "concept": period.concept,
            # Metadata de display SOLAMENTE (Días × 8) — `None` cuando la fuente
            # no trae Días válido; la barra igual se posiciona por start/end.
            "hours": period.hours,
            **position,
        })
    return {
        "absences": absences,
        "unmatched_count": len(join_result.unmatched),
        "ambiguous_count": len(join_result.ambiguous),
    }


class StaffingService:
    """Repository injectable para tests (mock sin tocar `core.microsoft365`)."""

    def __init__(self, repository: StaffingRepository | None = None):
        self._repository = repository if repository is not None else StaffingRepository()

    def get_overview(self, access_token: str, *, assignment_scope: str = "operational") -> list[StaffingOverviewRow]:
        records = self._repository.get_allocations(access_token, assignment_scope=assignment_scope)
        # Diagnóstico (pre-PR corrective, ver HANDOFF) — passthrough puro del
        # repositorio; `getattr` con default `{}` porque un test double sin
        # ese atributo (varios existen en tests/webapp/test_staffing_routes.py)
        # no debe romper get_overview.
        self.last_diagnostics = getattr(self._repository, "last_diagnostics", {})
        rows = [self._to_row(record) for record in records]
        # Orden estable por consultor luego proyecto — determinístico para
        # que el mismo dato fuente siempre rinda el mismo orden en pantalla.
        rows.sort(key=lambda row: (row.consultant_name, row.project_name))
        return rows

    def get_absence_overlay(self, access_token: str, *, today: date | None = None) -> dict:
        """Ausencias APROBADAS ya unidas contra el universo de Colaboradores
        VIGENTE, listas para overlay del timeline (Consultor únicamente, ver
        template/app.js). Lee Ausencias + Colaboradores por separado de
        `get_overview` — carga LAZY e independiente (ver HANDOFF), nunca una
        segunda pasada sobre Asignaciones."""
        absences = self._repository.get_absences(access_token)
        consultants = self._repository.get_consultants(access_token)
        self.last_diagnostics = getattr(self._repository, "last_diagnostics", {})
        join_result = join_absences_to_consultants(absences, consultants)
        return build_absence_overlay_payload(join_result, today=today)

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
            # Passthrough puro — client_id conserva el id crudo aunque
            # client_name no resuelva (mismo criterio que consultant_id/
            # project_id); client_name se queda None, sin fallback textual.
            client_id=record.client_id,
            client_name=record.client_name,
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
    effective_today = today or date.today()
    horizon_months = compute_horizon_months(effective_today)
    today_key = _month_key(effective_today.replace(day=1))
    horizon = [
        {"index": i, "label": _month_label(m), "is_current": _month_key(m) == today_key}
        for i, m in enumerate(horizon_months)
    ]
    today_marker_pct = compute_today_marker(effective_today, horizon_months)
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
            # Sin fallback textual (a diferencia de consultant_name/
            # project_name) — `None` cuando no resuelve, el consumidor
            # (app.js) ya trata esto como "ausente" (x-show / operador ||).
            "client_id": row.client_id,
            "client_name": row.client_name,
            "allocation_value": row.allocation_value,
            "allocation_label": _allocation_label(row),
            "allocation_unit": row.allocation_unit,
            "status": row.status,
            "start_date": row.start_date.isoformat() if row.start_date else None,
            "end_date": row.end_date.isoformat() if row.end_date else None,
            "availability": row.consultant_availability or _SIN_DATO,
            "activo_vencido": is_activo_vencido(row.status, row.end_date, today=effective_today),
            **position,
        })
    return {"assignments": assignments, "horizon": horizon, "today_marker_pct": today_marker_pct}
