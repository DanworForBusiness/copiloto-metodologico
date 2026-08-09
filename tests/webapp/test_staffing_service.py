"""
test_staffing_service.py — Contrato de StaffingService (Batch 2 de
aceleración post-Fase 2). Inyecta un repositorio falso (StaffingRepository
NO se toca, ni Graph) — el service no debería saber que existe SharePoint.
"""
from __future__ import annotations

from datetime import date

from webapp.services.staffing_repository import StaffingAllocationRecord
from webapp.services.staffing_service import StaffingService


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
