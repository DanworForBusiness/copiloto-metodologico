"""
test_staffing_absence_service.py — Contrato de identidad/join/overlay de
Ausencias Aprobadas (Staffing Approved Absence Overlay — generalización del
Vacation Timeline Overlay original, ver HANDOFF): normalización
determinística de nombre (SIN fuzzy-matching), join Ausencias↔Colaboradores
(matched/unmatched/ambiguous), payload de overlay reusando el MISMO
horizonte/posicionamiento día-exacto que Asignaciones, concepto YA
CLASIFICADO passthrough, y cero impacto en KPIs/capacidad existentes.
"""
from __future__ import annotations

from datetime import date

from webapp.services.staffing_repository import AbsencePeriod
from webapp.services.staffing_service import (
    AbsenceJoinResult,
    AbsenceOverviewPeriod,
    _normalize_consultant_name,
    build_absence_overlay_payload,
    join_absences_to_consultants,
)


# ============================================================================
# _normalize_consultant_name — identidad determinística
# ============================================================================

def test_normalize_trims_and_collapses_whitespace():
    assert _normalize_consultant_name("  Ana   Pérez  ") == _normalize_consultant_name("Ana Pérez")


def test_normalize_is_case_insensitive():
    assert _normalize_consultant_name("ANA PEREZ") == _normalize_consultant_name("ana perez")


def test_normalize_folds_accents_deterministically():
    assert _normalize_consultant_name("Matias Gómez") == _normalize_consultant_name("Matías Gómez")


def test_normalize_is_lossless_for_empty_or_none():
    assert _normalize_consultant_name("") == ""
    assert _normalize_consultant_name(None) == ""


def test_normalize_never_matches_genuinely_different_names():
    assert _normalize_consultant_name("Ana Pérez") != _normalize_consultant_name("Ana Perea")
    assert _normalize_consultant_name("Juan Pérez") != _normalize_consultant_name("Pérez Juan")  # sin reordenar


# ============================================================================
# join_absences_to_consultants
# ============================================================================

def _absence(**overrides):
    base = dict(
        source_id="1", consultant_name="Matías Gómez", start_date="2026-03-10",
        end_date="2026-03-20", concept="Vacaciones",
    )
    base.update(overrides)
    return AbsencePeriod(**base)


def test_exact_name_match_attaches_consultant_identity():
    result = join_absences_to_consultants([_absence()], [("c1", "Matías Gómez")])
    assert len(result.periods) == 1
    assert result.periods[0].consultant_id == "c1"
    assert result.periods[0].consultant_name == "Matías Gómez"
    assert result.unmatched == [] and result.ambiguous == []


def test_whitespace_normalized_match_attaches_consultant_identity():
    result = join_absences_to_consultants(
        [_absence(consultant_name="  Matías   Gómez  ")], [("c1", "Matías Gómez")],
    )
    assert len(result.periods) == 1 and result.periods[0].consultant_id == "c1"


def test_case_normalized_match_attaches_consultant_identity():
    result = join_absences_to_consultants(
        [_absence(consultant_name="MATÍAS GÓMEZ")], [("c1", "Matías Gómez")],
    )
    assert len(result.periods) == 1 and result.periods[0].consultant_id == "c1"


def test_accent_difference_matias_matches_deterministically():
    result = join_absences_to_consultants(
        [_absence(consultant_name="Matias Gomez")], [("c1", "Matías Gómez")],
    )
    assert len(result.periods) == 1 and result.periods[0].consultant_id == "c1"


def test_unmatched_name_does_not_attach_to_any_consultant():
    result = join_absences_to_consultants(
        [_absence(consultant_name="Nadie Conocido")], [("c1", "Matías Gómez")],
    )
    assert result.periods == []
    assert len(result.unmatched) == 1
    assert result.unmatched[0].consultant_name == "Nadie Conocido"


def test_ambiguous_normalized_identity_does_not_attach_to_either_consultant():
    result = join_absences_to_consultants(
        [_absence(consultant_name="Juan Pérez")],
        [("c1", "Juan Pérez"), ("c2", "juan perez")],
    )
    assert result.periods == []
    assert len(result.ambiguous) == 1


def test_malformed_or_missing_date_excludes_period_entirely():
    result = join_absences_to_consultants(
        [_absence(start_date=None, end_date="2026-03-20")], [("c1", "Matías Gómez")],
    )
    assert result.periods == [] and result.unmatched == [] and result.ambiguous == []


def test_another_consultant_with_no_absence_rows_is_simply_absent():
    result = join_absences_to_consultants([], [("c1", "Matías Gómez"), ("c2", "Ana Pérez")])
    assert result == AbsenceJoinResult(periods=[], unmatched=[], ambiguous=[])


# ============================================================================
# build_absence_overlay_payload — horizonte/posicionamiento día-exacta + no-PII
# ============================================================================

_TODAY = date(2026, 8, 11)


def _period(**overrides):
    base = dict(
        source_id="1", consultant_id="c1", consultant_name="Matías Gómez",
        start_date=date(2026, 8, 15), end_date=date(2026, 8, 20), concept="Vacaciones",
    )
    base.update(overrides)
    return AbsenceOverviewPeriod(**base)


def test_period_within_horizon_is_positioned():
    payload = build_absence_overlay_payload(AbsenceJoinResult(periods=[_period()], unmatched=[], ambiguous=[]), today=_TODAY)
    assert len(payload["absences"]) == 1
    v = payload["absences"][0]
    assert v["on_timeline"] is True
    assert v["consultant_id"] == "c1"
    assert v["start_date"] == "2026-08-15"
    assert v["end_date"] == "2026-08-20"


def test_single_day_period_is_positioned():
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(start_date=date(2026, 8, 15), end_date=date(2026, 8, 15))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    v = payload["absences"][0]
    # Día-exacta (ver HANDOFF): un período de un solo día sigue teniendo un
    # ancho positivo real (end_pct > start_pct), nunca un punto de ancho
    # cero que dependa 100% del min-width del CSS para ser visible.
    assert v["end_pct"] > v["start_pct"]


def test_multiple_periods_position_independently():
    periods = [
        _period(source_id="1", start_date=date(2026, 8, 15), end_date=date(2026, 8, 20)),
        _period(source_id="2", consultant_id="c2", consultant_name="Ana Pérez",
                start_date=date(2026, 10, 1), end_date=date(2026, 10, 5)),
    ]
    payload = build_absence_overlay_payload(AbsenceJoinResult(periods=periods, unmatched=[], ambiguous=[]), today=_TODAY)
    assert {v["source_id"] for v in payload["absences"]} == {"1", "2"}


# Un período íntegramente pasado, fuera del horizonte visible, NUNCA se
# clippea al borde (a diferencia de una asignación abierta) — se excluye por
# completo, para no sugerir que está vigente ahora.
def test_period_entirely_before_horizon_is_excluded_not_clipped():
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(start_date=date(2020, 1, 1), end_date=date(2020, 1, 10))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert payload["absences"] == []


def test_period_entirely_after_horizon_is_excluded_not_clipped():
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(start_date=date(2030, 1, 1), end_date=date(2030, 1, 10))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert payload["absences"] == []


def test_period_partially_overlapping_horizon_start_is_clipped():
    # Arranca antes del horizonte visible (horizonte = jul..feb desde
    # 2026-08-11) pero termina dentro — SÍ debe aparecer, clippeado al borde.
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(start_date=date(2026, 1, 1), end_date=date(2026, 7, 15))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert len(payload["absences"]) == 1
    assert payload["absences"][0]["clipped_start"] is True


# Geometría día-exacta reusada (ver HANDOFF — timeline geometry corrective):
# las ausencias usan EXACTAMENTE el mismo compute_timeline_position que las
# asignaciones y el marcador de "hoy" — nunca una segunda implementación.
def test_absence_geometry_reuses_the_canonical_day_accurate_position():
    from webapp.services.staffing_service import compute_horizon_months, compute_timeline_position
    horizon = compute_horizon_months(_TODAY)
    expected = compute_timeline_position(date(2026, 8, 15), date(2026, 8, 20), horizon)
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(start_date=date(2026, 8, 15), end_date=date(2026, 8, 20))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    v = payload["absences"][0]
    assert (v["start_pct"], v["end_pct"], v["on_timeline"], v["clipped_start"], v["clipped_end"]) == (
        expected["start_pct"], expected["end_pct"], expected["on_timeline"],
        expected["clipped_start"], expected["clipped_end"],
    )


# Conteos puros de diagnóstico — nunca nombres.
def test_unmatched_and_ambiguous_counts_are_pure_counts_no_names():
    unmatched_absence = AbsencePeriod(source_id="9", consultant_name="Nadie", start_date="2026-01-01", end_date="2026-01-02", concept="Otros")
    result = AbsenceJoinResult(periods=[], unmatched=[unmatched_absence], ambiguous=[unmatched_absence])
    payload = build_absence_overlay_payload(result, today=_TODAY)
    assert payload["unmatched_count"] == 1
    assert payload["ambiguous_count"] == 1
    assert "Nadie" not in str(payload)


# Régimen de no-impacto: el payload de ausencias no incluye NINGUNA de las
# claves de KPI/capacidad de Asignaciones — es un objeto completamente
# separado, nunca mezclado con build_dashboard_payload.
def test_absence_payload_has_no_kpi_or_allocation_keys():
    payload = build_absence_overlay_payload(AbsenceJoinResult(periods=[_period()], unmatched=[], ambiguous=[]), today=_TODAY)
    assert set(payload.keys()) == {"absences", "unmatched_count", "ambiguous_count"}
    absence_row = payload["absences"][0]
    for forbidden in ("allocation_value", "allocation_unit", "status", "activo_vencido"):
        assert forbidden not in absence_row


# ============================================================================
# concept — passthrough YA CLASIFICADO server-side, privacy boundary
# ============================================================================

def test_join_passes_concept_through_to_overview_period():
    result = join_absences_to_consultants([_absence(concept="Danwor Day")], [("c1", "Matías Gómez")])
    assert result.periods[0].concept == "Danwor Day"


def test_payload_emits_concept_for_every_absence():
    for concept in ("Vacaciones", "Danwor Day", "Cumpleaños", "Otros"):
        payload = build_absence_overlay_payload(
            AbsenceJoinResult(periods=[_period(concept=concept)], unmatched=[], ambiguous=[]), today=_TODAY,
        )
        assert payload["absences"][0]["concept"] == concept


def test_payload_never_includes_a_raw_motivo_field():
    # Privacy boundary (ver HANDOFF): ninguna key del payload puede
    # llamarse motivo/raw_motivo — el service ni siquiera CONOCE el crudo,
    # solo transporta `concept` (ya clasificado en el repositorio).
    payload = build_absence_overlay_payload(AbsenceJoinResult(periods=[_period()], unmatched=[], ambiguous=[]), today=_TODAY)
    row = payload["absences"][0]
    assert "motivo" not in row and "raw_motivo" not in row and "Motivo" not in row


# ============================================================================
# hours — passthrough de DISPLAY (Días × 8), nunca allocation/KPI
# ============================================================================

def test_join_passes_hours_through_to_overview_period():
    result = join_absences_to_consultants([_absence(hours=80)], [("c1", "Matías Gómez")])
    assert result.periods[0].hours == 80


def test_join_preserves_none_hours():
    result = join_absences_to_consultants([_absence(hours=None)], [("c1", "Matías Gómez")])
    assert result.periods[0].hours is None


def test_payload_emits_hours_when_available():
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(hours=80)], unmatched=[], ambiguous=[]), today=_TODAY,
    )
    assert payload["absences"][0]["hours"] == 80


def test_payload_emits_none_hours_when_absent():
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(hours=None)], unmatched=[], ambiguous=[]), today=_TODAY,
    )
    assert payload["absences"][0]["hours"] is None


# malformed/missing hours (Días) NEVER suppresses a valid bar — the period
# still renders, geometry is untouched, only the displayed hours total is
# affected (director-approved rule).
def test_missing_hours_does_not_suppress_the_bar():
    payload = build_absence_overlay_payload(
        AbsenceJoinResult(periods=[_period(hours=None)], unmatched=[], ambiguous=[]), today=_TODAY,
    )
    assert len(payload["absences"]) == 1
    assert payload["absences"][0]["on_timeline"] is True
