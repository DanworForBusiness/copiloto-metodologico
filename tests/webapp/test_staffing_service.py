"""
test_staffing_service.py — Contrato de StaffingService (Batch 2 de
aceleración post-Fase 2). Inyecta un repositorio falso (StaffingRepository
NO se toca, ni Graph) — el service no debería saber que existe SharePoint.
"""
from __future__ import annotations

from datetime import date

from webapp.services.staffing_repository import StaffingAllocationRecord
from webapp.services.staffing_service import (
    StaffingService,
    build_dashboard_payload,
    compute_horizon_months,
    compute_timeline_position,
)


class _FakeRepository:
    def __init__(self, records):
        self._records = records

    def get_allocations(self, access_token):
        self.last_token = access_token
        return self._records


def _record(**overrides):
    base = dict(
        assignment_id="1",
        consultant_id="1",
        consultant_name="Zulema",
        project_id="10",
        project_name="Proyecto Z",
        allocation_value=50.0,
        allocation_unit="hours_per_month",
        status="Active",
        start_date="2026-01-01",
        end_date="2026-06-30",
    )
    base.update(overrides)
    return StaffingAllocationRecord(**base)


def test_normalized_records_become_stable_overview_rows():
    service = StaffingService(repository=_FakeRepository([_record()]))
    rows = service.get_overview("tok")
    assert len(rows) == 1
    row = rows[0]
    assert row.assignment_id == "1"
    assert row.consultant_name == "Zulema"
    assert row.project_name == "Proyecto Z"
    assert row.allocation_value == 50.0
    assert row.allocation_unit == "hours_per_month"


def test_capacity_percentage_stays_none_even_though_unit_is_now_confirmed():
    # allocation_unit ya es "hours_per_month" (validado, Batch 3) — pero
    # capacity_percentage sigue None a propósito: la SEMÁNTICA de capacidad
    # (no solo la unidad) queda para el próximo incremento funcional.
    service = StaffingService(repository=_FakeRepository([_record()]))
    row = service.get_overview("tok")[0]
    assert row.allocation_unit == "hours_per_month"
    assert row.capacity_percentage is None


def test_deterministic_ordering_by_consultant_then_project():
    records = [
        _record(assignment_id="1", consultant_name="Zulema", project_name="B"),
        _record(assignment_id="2", consultant_name="Ana", project_name="Z"),
        _record(assignment_id="3", consultant_name="Ana", project_name="A"),
    ]
    service = StaffingService(repository=_FakeRepository(records))
    rows = service.get_overview("tok")
    assert [(r.consultant_name, r.project_name) for r in rows] == [
        ("Ana", "A"),
        ("Ana", "Z"),
        ("Zulema", "B"),
    ]


def test_status_and_dates_remain_semantically_correct():
    record = _record(status="Tentative", start_date="2026-03-15T00:00:00Z", end_date=None)
    service = StaffingService(repository=_FakeRepository([record]))
    row = service.get_overview("tok")[0]
    assert row.status == "Tentative"
    assert row.start_date == date(2026, 3, 15)
    assert row.end_date is None


def test_unresolved_names_display_as_a_clear_placeholder():
    record = _record(consultant_name=None, project_name=None)
    service = StaffingService(repository=_FakeRepository([record]))
    row = service.get_overview("tok")[0]
    assert row.consultant_name == "(sin resolver)"
    assert row.project_name == "(sin resolver)"


def test_empty_repository_result_gives_empty_read_model_not_an_exception():
    service = StaffingService(repository=_FakeRepository([]))
    rows = service.get_overview("tok")
    assert rows == []


def test_access_token_forwarded_to_repository_unchanged():
    fake_repo = _FakeRepository([_record()])
    service = StaffingService(repository=fake_repo)
    service.get_overview("tok-explicit")
    assert fake_repo.last_token == "tok-explicit"


# --- Enriquecimiento OPCIONAL FT/PT (Asignaciones Dashboard v1) ------------

def test_consultant_availability_passthrough():
    record = _record(consultant_availability="Full Time")
    service = StaffingService(repository=_FakeRepository([record]))
    row = service.get_overview("tok")[0]
    assert row.consultant_availability == "Full Time"


def test_consultant_availability_none_stays_none_at_this_layer():
    # Sin fallback sintético acá — "Sin dato" es una decisión de
    # PRESENTACIÓN de build_dashboard_payload, no del read-model.
    record = _record(consultant_availability=None)
    service = StaffingService(repository=_FakeRepository([record]))
    row = service.get_overview("tok")[0]
    assert row.consultant_availability is None


def test_consultant_and_project_ids_survive_to_overview_row():
    record = _record(consultant_id="c-1", project_id="p-10")
    service = StaffingService(repository=_FakeRepository([record]))
    row = service.get_overview("tok")[0]
    assert row.consultant_id == "c-1"
    assert row.project_id == "p-10"


# --- Horizonte mensual del timeline (función pura) -------------------------

def test_horizon_months_are_prev_current_plus_six_months():
    horizon = compute_horizon_months(date(2026, 3, 15))
    assert horizon == [
        date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1),
        date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1),
    ]


def test_horizon_months_cross_year_boundary():
    horizon = compute_horizon_months(date(2026, 1, 10))
    assert horizon[0] == date(2025, 12, 1)
    assert horizon[-1] == date(2026, 7, 1)
    assert len(horizon) == 8


# --- Posición en el timeline (función pura, sin aritmética de píxeles) -----

_HORIZON = compute_horizon_months(date(2026, 3, 15))  # feb..sep 2026


def test_position_entirely_within_horizon():
    pos = compute_timeline_position(date(2026, 4, 1), date(2026, 6, 30), _HORIZON)
    assert pos == {"on_timeline": True, "start_index": 2, "end_index": 4, "clipped_start": False, "clipped_end": False}


def test_position_starts_before_horizon_clips_to_first_month():
    pos = compute_timeline_position(date(2025, 1, 1), date(2026, 4, 30), _HORIZON)
    assert pos["on_timeline"] is True
    assert pos["start_index"] == 0
    assert pos["clipped_start"] is True
    assert pos["clipped_end"] is False


def test_position_ends_after_horizon_clips_to_last_month():
    pos = compute_timeline_position(date(2026, 8, 1), date(2027, 3, 1), _HORIZON)
    assert pos["end_index"] == 7
    assert pos["clipped_end"] is True
    assert pos["clipped_start"] is False


def test_position_spans_complete_horizon():
    pos = compute_timeline_position(date(2020, 1, 1), date(2030, 1, 1), _HORIZON)
    assert pos == {"on_timeline": True, "start_index": 0, "end_index": 7, "clipped_start": True, "clipped_end": True}


def test_position_future_assignment_within_horizon():
    pos = compute_timeline_position(date(2026, 9, 1), date(2026, 9, 30), _HORIZON)
    assert pos["start_index"] == 7
    assert pos["end_index"] == 7
    assert pos["clipped_start"] is False
    assert pos["clipped_end"] is False


def test_position_both_dates_missing_excluded_from_timeline_only():
    pos = compute_timeline_position(None, None, _HORIZON)
    assert pos["on_timeline"] is False


def test_position_missing_start_treated_as_open_ended_left():
    pos = compute_timeline_position(None, date(2026, 4, 30), _HORIZON)
    assert pos["on_timeline"] is True
    assert pos["start_index"] == 0
    assert pos["clipped_start"] is True
    assert pos["end_index"] == 2


def test_position_missing_end_treated_as_open_ended_right():
    pos = compute_timeline_position(date(2026, 5, 1), None, _HORIZON)
    assert pos["on_timeline"] is True
    assert pos["end_index"] == 7
    assert pos["clipped_end"] is True
    assert pos["start_index"] == 3


# --- Payload del dashboard (transformación pura, sin Graph/repositorio) ----

def test_dashboard_payload_shape_and_allocation_label():
    record = _record(allocation_value=40.0, allocation_unit="hours_per_month")
    rows = StaffingService(repository=_FakeRepository([record])).get_overview("tok")
    payload = build_dashboard_payload(rows, today=date(2026, 3, 15))
    row = payload["assignments"][0]
    assert row["allocation_label"] == "40 h/mes"
    assert len(payload["horizon"]) == 8


def test_dashboard_payload_availability_defaults_to_sin_dato():
    record = _record(consultant_availability=None)
    rows = StaffingService(repository=_FakeRepository([record])).get_overview("tok")
    payload = build_dashboard_payload(rows, today=date(2026, 3, 15))
    assert payload["assignments"][0]["availability"] == "Sin dato"


def test_dashboard_payload_preserves_real_availability_value():
    record = _record(consultant_availability="Part Time")
    rows = StaffingService(repository=_FakeRepository([record])).get_overview("tok")
    payload = build_dashboard_payload(rows, today=date(2026, 3, 15))
    assert payload["assignments"][0]["availability"] == "Part Time"


def test_dashboard_payload_multiple_assignments_same_consultant_each_get_own_position():
    records = [
        _record(assignment_id="1", consultant_id="c1", start_date="2026-02-01", end_date="2026-03-31"),
        _record(assignment_id="2", consultant_id="c1", start_date="2026-05-01", end_date="2026-06-30"),
    ]
    rows = StaffingService(repository=_FakeRepository(records)).get_overview("tok")
    payload = build_dashboard_payload(rows, today=date(2026, 3, 15))
    by_id = {a["assignment_id"]: a for a in payload["assignments"]}
    assert by_id["1"]["start_index"] != by_id["2"]["start_index"]
    assert by_id["1"]["consultant_id"] == by_id["2"]["consultant_id"] == "c1"


def test_dashboard_payload_horizon_marks_current_month():
    rows = StaffingService(repository=_FakeRepository([_record()])).get_overview("tok")
    payload = build_dashboard_payload(rows, today=date(2026, 3, 15))
    current = [m for m in payload["horizon"] if m["is_current"]]
    assert len(current) == 1
    assert current[0]["label"] == "mar 2026"


def test_dashboard_payload_no_capacity_fields_introduced():
    rows = StaffingService(repository=_FakeRepository([_record()])).get_overview("tok")
    payload = build_dashboard_payload(rows, today=date(2026, 3, 15))
    row = payload["assignments"][0]
    assert "capacity_percentage" not in row
    assert not any("capacity" in key for key in row)
