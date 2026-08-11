"""
test_staffing_vacation_service.py — Contrato de identidad/join/overlay de
Vacaciones (Staffing Vacation Timeline Overlay, ver HANDOFF): normalización
determinística de nombre (SIN fuzzy-matching), join Vacaciones↔Colaboradores
(matched/unmatched/ambiguous), payload de overlay reusando el MISMO
horizonte/posicionamiento que Asignaciones, y cero impacto en KPIs/capacidad
existentes.
"""
from __future__ import annotations

from datetime import date

from webapp.services.staffing_repository import VacationPeriod
from webapp.services.staffing_service import (
    VacationJoinResult,
    VacationOverviewPeriod,
    _normalize_consultant_name,
    build_vacation_overlay_payload,
    join_vacations_to_consultants,
)


# ============================================================================
# _normalize_consultant_name — identidad determinística
# ============================================================================

# test 11: whitespace
def test_normalize_trims_and_collapses_whitespace():
    assert _normalize_consultant_name("  Ana   Pérez  ") == _normalize_consultant_name("Ana Pérez")


# test 12: case
def test_normalize_is_case_insensitive():
    assert _normalize_consultant_name("ANA PEREZ") == _normalize_consultant_name("ana perez")


# test 13: accent difference (Matias/Matías) resuelve igual — el caso real
# disclosed por el owner.
def test_normalize_folds_accents_deterministically():
    assert _normalize_consultant_name("Matias Gómez") == _normalize_consultant_name("Matías Gómez")


def test_normalize_is_lossless_for_empty_or_none():
    assert _normalize_consultant_name("") == ""
    assert _normalize_consultant_name(None) == ""


# test 16: SIN fuzzy-matching — nombres genuinamente distintos (más allá de
# whitespace/case/acentos) nunca normalizan igual.
def test_normalize_never_matches_genuinely_different_names():
    assert _normalize_consultant_name("Ana Pérez") != _normalize_consultant_name("Ana Perea")
    assert _normalize_consultant_name("Juan Pérez") != _normalize_consultant_name("Pérez Juan")  # sin reordenar


# ============================================================================
# join_vacations_to_consultants
# ============================================================================

def _vacation(**overrides):
    base = dict(source_id="1", consultant_name="Matías Gómez", start_date="2026-03-10", end_date="2026-03-20")
    base.update(overrides)
    return VacationPeriod(**base)


# test 10: match exacto.
def test_exact_name_match_attaches_consultant_identity():
    result = join_vacations_to_consultants([_vacation()], [("c1", "Matías Gómez")])
    assert len(result.periods) == 1
    assert result.periods[0].consultant_id == "c1"
    assert result.periods[0].consultant_name == "Matías Gómez"
    assert result.unmatched == [] and result.ambiguous == []


# test 11 (a nivel de join)
def test_whitespace_normalized_match_attaches_consultant_identity():
    result = join_vacations_to_consultants(
        [_vacation(consultant_name="  Matías   Gómez  ")], [("c1", "Matías Gómez")],
    )
    assert len(result.periods) == 1 and result.periods[0].consultant_id == "c1"


# test 12 (a nivel de join)
def test_case_normalized_match_attaches_consultant_identity():
    result = join_vacations_to_consultants(
        [_vacation(consultant_name="MATÍAS GÓMEZ")], [("c1", "Matías Gómez")],
    )
    assert len(result.periods) == 1 and result.periods[0].consultant_id == "c1"


# test 13 (a nivel de join) — el caso disclosed: Matias (vacaciones) vs
# Matías (Colaboradores).
def test_accent_difference_matias_matches_deterministically():
    result = join_vacations_to_consultants(
        [_vacation(consultant_name="Matias Gomez")], [("c1", "Matías Gómez")],
    )
    assert len(result.periods) == 1 and result.periods[0].consultant_id == "c1"


# test 14: nombre sin match no se adjunta a nadie.
def test_unmatched_name_does_not_attach_to_any_consultant():
    result = join_vacations_to_consultants(
        [_vacation(consultant_name="Nadie Conocido")], [("c1", "Matías Gómez")],
    )
    assert result.periods == []
    assert len(result.unmatched) == 1
    assert result.unmatched[0].consultant_name == "Nadie Conocido"


# test 15: identidad normalizada ambigua (2 consultores distintos con el
# mismo nombre normalizado) no se adjunta a NINGUNO.
def test_ambiguous_normalized_identity_does_not_attach_to_either_consultant():
    result = join_vacations_to_consultants(
        [_vacation(consultant_name="Juan Pérez")],
        [("c1", "Juan Pérez"), ("c2", "juan perez")],
    )
    assert result.periods == []
    assert len(result.ambiguous) == 1


def test_malformed_or_missing_date_excludes_period_entirely():
    result = join_vacations_to_consultants(
        [_vacation(start_date=None, end_date="2026-03-20")], [("c1", "Matías Gómez")],
    )
    assert result.periods == [] and result.unmatched == [] and result.ambiguous == []


def test_another_consultant_with_no_vacation_rows_is_simply_absent():
    result = join_vacations_to_consultants([], [("c1", "Matías Gómez"), ("c2", "Ana Pérez")])
    assert result == VacationJoinResult(periods=[], unmatched=[], ambiguous=[])


# ============================================================================
# build_vacation_overlay_payload — horizonte/posicionamiento + no-PII
# ============================================================================

_TODAY = date(2026, 8, 11)


def _period(**overrides):
    base = dict(
        source_id="1", consultant_id="c1", consultant_name="Matías Gómez",
        start_date=date(2026, 8, 15), end_date=date(2026, 8, 20),
    )
    base.update(overrides)
    return VacationOverviewPeriod(**base)


# test 17/20/21: se posiciona con el MISMO horizonte que Asignaciones,
# múltiples períodos se posicionan de forma independiente, un período de un
# solo día es válido.
def test_period_within_horizon_is_positioned():
    payload = build_vacation_overlay_payload(VacationJoinResult(periods=[_period()], unmatched=[], ambiguous=[]), today=_TODAY)
    assert len(payload["vacations"]) == 1
    v = payload["vacations"][0]
    assert v["on_timeline"] is True
    assert v["consultant_id"] == "c1"
    assert v["start_date"] == "2026-08-15"
    assert v["end_date"] == "2026-08-20"


def test_single_day_period_is_positioned():
    payload = build_vacation_overlay_payload(
        VacationJoinResult(periods=[_period(start_date=date(2026, 8, 15), end_date=date(2026, 8, 15))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert payload["vacations"][0]["start_index"] == payload["vacations"][0]["end_index"]


def test_multiple_periods_position_independently():
    periods = [
        _period(source_id="1", start_date=date(2026, 8, 15), end_date=date(2026, 8, 20)),
        _period(source_id="2", consultant_id="c2", consultant_name="Ana Pérez",
                start_date=date(2026, 10, 1), end_date=date(2026, 10, 5)),
    ]
    payload = build_vacation_overlay_payload(VacationJoinResult(periods=periods, unmatched=[], ambiguous=[]), today=_TODAY)
    assert {v["source_id"] for v in payload["vacations"]} == {"1", "2"}


# Un período íntegramente pasado, fuera del horizonte visible, NUNCA se
# clippea al borde (a diferencia de una asignación abierta) — se excluye por
# completo, para no sugerir que está vigente ahora.
def test_period_entirely_before_horizon_is_excluded_not_clipped():
    payload = build_vacation_overlay_payload(
        VacationJoinResult(periods=[_period(start_date=date(2020, 1, 1), end_date=date(2020, 1, 10))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert payload["vacations"] == []


def test_period_entirely_after_horizon_is_excluded_not_clipped():
    payload = build_vacation_overlay_payload(
        VacationJoinResult(periods=[_period(start_date=date(2030, 1, 1), end_date=date(2030, 1, 10))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert payload["vacations"] == []


def test_period_partially_overlapping_horizon_start_is_clipped():
    # Arranca antes del horizonte visible (horizonte = jul..feb desde
    # 2026-08-11) pero termina dentro — SÍ debe aparecer, clippeado al borde.
    payload = build_vacation_overlay_payload(
        VacationJoinResult(periods=[_period(start_date=date(2026, 1, 1), end_date=date(2026, 7, 15))], unmatched=[], ambiguous=[]),
        today=_TODAY,
    )
    assert len(payload["vacations"]) == 1
    assert payload["vacations"][0]["clipped_start"] is True


# Conteos puros de diagnóstico — nunca nombres.
def test_unmatched_and_ambiguous_counts_are_pure_counts_no_names():
    unmatched_vac = VacationPeriod(source_id="9", consultant_name="Nadie", start_date="2026-01-01", end_date="2026-01-02")
    result = VacationJoinResult(periods=[], unmatched=[unmatched_vac], ambiguous=[unmatched_vac])
    payload = build_vacation_overlay_payload(result, today=_TODAY)
    assert payload["unmatched_count"] == 1
    assert payload["ambiguous_count"] == 1
    assert "Nadie" not in str(payload)


# Régimen de no-impacto: el payload de vacaciones no incluye NINGUNA de las
# claves de KPI/capacidad de Asignaciones — es un objeto completamente
# separado, nunca mezclado con build_dashboard_payload.
def test_vacation_payload_has_no_kpi_or_allocation_keys():
    payload = build_vacation_overlay_payload(VacationJoinResult(periods=[_period()], unmatched=[], ambiguous=[]), today=_TODAY)
    assert set(payload.keys()) == {"vacations", "unmatched_count", "ambiguous_count"}
    vacation_row = payload["vacations"][0]
    for forbidden in ("allocation_value", "allocation_unit", "status", "activo_vencido"):
        assert forbidden not in vacation_row
