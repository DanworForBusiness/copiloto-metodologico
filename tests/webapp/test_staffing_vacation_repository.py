"""
test_staffing_vacation_repository.py — Contrato de StaffingRepository para
Vacaciones (Staffing Vacation Timeline Overlay, ver HANDOFF). Determinístico,
sin Graph real: monkeypatchea `core.microsoft365.graph_get_paged_values` con
fixtures fabricadas — mismo criterio que test_staffing_repository.py.

⚠️ MAPEO DE CAMPOS DE FIXTURE DE TEST — NO SON NOMBRES REALES DE PRODUCCIÓN.
`_TEST_VACATION_MAPPING` es un `VacationFieldMapping` inventado para este test
únicamente. `VacationFieldMapping.from_config()` (producción) sigue vacío
hasta confirmar contra Graph real (ver config.py) — el offline contract
(sheet "Solicitudes" del export real del owner) valida SOLO los VALORES de
negocio (Motivo/Estado), nunca los internal names.
"""
from __future__ import annotations

import pytest

from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_repository import (
    StaffingRepository,
    VacationFieldMapping,
    VacationPeriod,
)

_SITE_ID = "test-site-id"
_VACA_LIST = "vaca-list-id"
_COLAB_LIST = "colab-list-id"

_TEST_VACATION_MAPPING = VacationFieldMapping(
    apellido="Apellido", nombre="Nombre", motivo="Motivo",
    desde="Desde", hasta="Hasta", estado="Estado",
)


def _make_repository(vacation_field_mapping=_TEST_VACATION_MAPPING):
    return StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        vacaciones_list_id=_VACA_LIST, vacation_field_mapping=vacation_field_mapping,
    )


# --- VacationFieldMapping ----------------------------------------------------

# test: sin default de producción — todos vacíos por config.
def test_vacation_field_mapping_from_config_has_no_production_default():
    mapping = VacationFieldMapping.from_config()
    assert mapping.missing_fields() == [
        "apellido", "nombre", "motivo", "desde", "hasta", "estado",
    ]


def test_vacation_field_mapping_missing_fields_empty_when_complete():
    assert _TEST_VACATION_MAPPING.missing_fields() == []


def test_get_vacations_raises_explicit_when_mapping_incomplete():
    repo = _make_repository(vacation_field_mapping=VacationFieldMapping(
        apellido="", nombre="Nombre", motivo="Motivo", desde="Desde", hasta="Hasta", estado="Estado",
    ))
    with pytest.raises(RuntimeError, match="Vacaciones"):
        repo.get_vacations("tok")


def test_get_consultants_raises_explicit_when_colaborador_title_missing(monkeypatch):
    from webapp.services.staffing_repository import StaffingFieldMapping
    repo = StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        field_mapping=StaffingFieldMapping(
            colaborador_lookup_id="x", proyecto_lookup_id="x", allocation="x", status="x",
            start_date="x", end_date="x", proyecto_title="x", colaborador_title="",
        ),
    )
    with pytest.raises(RuntimeError):
        repo.get_consultants("tok")


# --- get_vacations: filtro Motivo=Vacaciones + Estado=Aprobada -------------

_APPROVED = {
    "id": "1", "fields": {
        "Nombre": "Matías", "Apellido": "Gómez", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-03-10", "Hasta": "2026-03-20",
    },
}
_CANCELLED = {
    "id": "2", "fields": {
        "Nombre": "Ana", "Apellido": "Pérez", "Motivo": "Vacaciones",
        "Estado": "Cancelada", "Desde": "2026-11-01", "Hasta": "2026-11-10",
    },
}
_REJECTED = {
    "id": "3", "fields": {
        "Nombre": "Beto", "Apellido": "Ruiz", "Motivo": "Vacaciones",
        "Estado": "Rechazada", "Desde": "2026-05-01", "Hasta": "2026-05-05",
    },
}
_OTHER_REASON = {
    "id": "4", "fields": {
        "Nombre": "Carla", "Apellido": "Diaz", "Motivo": "Enfermedad",
        "Estado": "Aprobada", "Desde": "2026-05-01", "Hasta": "2026-05-05",
    },
}
_MISSING_END_DATE = {
    "id": "5", "fields": {
        "Nombre": "Dario", "Apellido": "Suarez", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-06-01", "Hasta": "",
    },
}
_MISSING_NAME = {
    "id": "6", "fields": {
        "Nombre": "", "Apellido": "", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-06-01", "Hasta": "2026-06-05",
    },
}
_SINGLE_DAY = {
    "id": "7", "fields": {
        "Nombre": "Elena", "Apellido": "Torres", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-06-10", "Hasta": "2026-06-10",
    },
}
_SECOND_PERIOD_SAME_CONSULTANT = {
    "id": "8", "fields": {
        "Nombre": "Matías", "Apellido": "Gómez", "Motivo": "Vacaciones",
        "Estado": "Aprobada", "Desde": "2026-01-05", "Hasta": "2026-01-10",
    },
}


def _fake_fetch(rows):
    def fake(url, access_token, **kwargs):
        assert _SITE_ID in url and _VACA_LIST in url
        return rows
    return fake


# test 1: aprobada+Vacaciones se incluye.
def test_approved_vacation_becomes_vacation_period(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_APPROVED]))
    periods = _make_repository().get_vacations("tok")
    assert len(periods) == 1
    assert periods[0] == VacationPeriod(
        source_id="1", consultant_name="Matías Gómez", start_date="2026-03-10", end_date="2026-03-20",
    )


# test 2: Cancelada excluida.
def test_cancelled_vacation_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_CANCELLED]))
    assert _make_repository().get_vacations("tok") == []


# test 3: Rechazada excluida.
def test_rejected_vacation_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_REJECTED]))
    assert _make_repository().get_vacations("tok") == []


# test 4: otro Motivo (no Vacaciones) excluido.
def test_non_vacation_reason_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_OTHER_REASON]))
    assert _make_repository().get_vacations("tok") == []


# test 9: fecha faltante -> fila excluida, fail-soft (no rompe el resto).
def test_missing_end_date_row_excluded_fail_soft(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_MISSING_END_DATE, _APPROVED]),
    )
    periods = _make_repository().get_vacations("tok")
    assert [p.source_id for p in periods] == ["1"]


def test_missing_consultant_name_row_excluded(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_MISSING_NAME]))
    assert _make_repository().get_vacations("tok") == []


# test 6: vacaciones de un solo día son válidas.
def test_single_day_vacation_is_valid(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_SINGLE_DAY]))
    periods = _make_repository().get_vacations("tok")
    assert len(periods) == 1
    assert periods[0].start_date == periods[0].end_date == "2026-06-10"


# test 7: múltiples períodos para el MISMO consultor se preservan por
# separado — nunca se colapsan en un solo rango.
def test_multiple_periods_for_same_consultant_preserved_separately(monkeypatch):
    monkeypatch.setattr(
        repo_module.microsoft365, "graph_get_paged_values",
        _fake_fetch([_APPROVED, _SECOND_PERIOD_SAME_CONSULTANT]),
    )
    periods = _make_repository().get_vacations("tok")
    assert len(periods) == 2
    assert {p.source_id for p in periods} == {"1", "8"}
    assert all(p.consultant_name == "Matías Gómez" for p in periods)


# test 8: source item ID preservado.
def test_source_id_preserved_from_graph_item_id(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_APPROVED]))
    periods = _make_repository().get_vacations("tok")
    assert periods[0].source_id == "1"


def test_get_vacations_diagnostics_never_include_names_or_pii(monkeypatch):
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", _fake_fetch([_APPROVED]))
    repo = _make_repository()
    repo.get_vacations("tok-secret")
    diag = repo.last_diagnostics["vacaciones"]
    assert set(diag.keys()) == {"rows", "pages", "elapsed_ms"}
    assert diag["rows"] == 1


# --- get_consultants ---------------------------------------------------------

def test_get_consultants_reads_only_colaboradores(monkeypatch):
    from webapp.services.staffing_repository import StaffingFieldMapping
    calls = []

    def fake(url, access_token, **kwargs):
        calls.append(url)
        return [{"id": "1", "fields": {"Title": "Ana Pérez"}}, {"id": "2", "fields": {"Title": "Beto Ruiz"}}]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        field_mapping=StaffingFieldMapping(
            colaborador_lookup_id="x", proyecto_lookup_id="x", allocation="x", status="x",
            start_date="x", end_date="x", proyecto_title="x", colaborador_title="Title",
        ),
    )
    result = repo.get_consultants("tok")
    assert result == [("1", "Ana Pérez"), ("2", "Beto Ruiz")]
    assert len(calls) == 1
    assert _COLAB_LIST in calls[0]


def test_get_consultants_excludes_rows_without_id_or_name(monkeypatch):
    from webapp.services.staffing_repository import StaffingFieldMapping

    def fake(url, access_token, **kwargs):
        return [{"id": "1", "fields": {"Title": ""}}, {"id": "", "fields": {"Title": "Nadie"}}]

    monkeypatch.setattr(repo_module.microsoft365, "graph_get_paged_values", fake)
    repo = StaffingRepository(
        site_id=_SITE_ID, colaboradores_list_id=_COLAB_LIST,
        field_mapping=StaffingFieldMapping(
            colaborador_lookup_id="x", proyecto_lookup_id="x", allocation="x", status="x",
            start_date="x", end_date="x", proyecto_title="x", colaborador_title="Title",
        ),
    )
    assert repo.get_consultants("tok") == []
