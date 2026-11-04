from copy import deepcopy

import pytest

from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_service as service


@pytest.fixture
def columns():
    return [{"name": service.FUNNEL, "readOnly": False, "choice": {"choices": [
        admission.REQUESTED, admission.APPROVED, admission.REJECTED,
    ]}}]


def fields(**changes):
    return {service.FUNNEL: admission.REQUESTED, service.STATUS: "Pendiente", **changes}


def patch(original, submitted):
    return service.changes(service.form_values(original), submitted, {
        service.STATUS: ["Pendiente", "Planificado", "En curso", "Finalizado", "Suspendido"],
        service.PROJECT: {"4": "Proyecto simulado"}, service.ASSIGNEES: {"7": "Persona simulada"},
    })


@pytest.mark.parametrize('submitted', [
    {service.PLANNED: '2026-11-04'}, {service.ACTUAL: '2026-11-04'},
    *[{service.STATUS: state} for state in ('Planificado', 'En curso', 'Finalizado')],
])
def test_actual_commitment_adds_admission_to_one_patch(columns, submitted):
    original = fields()
    operational = patch(original, submitted)
    snapshot = deepcopy(operational)
    result = admission.approval_patch(original, submitted, operational, columns, confirmed=True)
    assert result == {**snapshot, service.FUNNEL: admission.APPROVED}
    assert operational == snapshot
    with pytest.raises(ValueError, match='¿Continuar?'):
        admission.approval_patch(original, submitted, operational, columns, confirmed=False)


@pytest.mark.parametrize('submitted', [
    {service.STATUS: 'Suspendido'}, {'Prioridad': 'Alta'}, {service.PROJECT: '4'},
    {service.ASSIGNEES: ['7']}, {service.DESCRIPTION: 'Texto'}, {service.COMMENTS: 'Comentario'}, {},
])
def test_other_changes_do_not_approve(columns, submitted):
    original = fields()
    operational = patch(original, submitted)
    assert admission.approval_patch(original, submitted, operational, columns, confirmed=False) == operational


@pytest.mark.parametrize('key', [service.PLANNED, service.ACTUAL])
def test_existing_date_changed_or_removed_is_not_first_commitment(columns, key):
    original = fields(**{key: '2026-11-01T00:00:00Z', service.STATUS: 'En curso'})
    for value in ('2026-11-02', '', '2026-11-01'):
        submitted = {key: value, service.STATUS: 'En curso'}
        operational = patch(original, submitted)
        assert not admission.proposes_approval(original, submitted, operational)
    submitted = {key: '2026-11-02', service.STATUS: 'Finalizado'}
    assert admission.proposes_approval(original, submitted, patch(original, submitted))


@pytest.mark.parametrize('funnel', [admission.APPROVED, admission.REJECTED, '', None, 'Otro valor'])
def test_existing_decision_or_unknown_value_is_not_overwritten(columns, funnel):
    original = fields(**{service.FUNNEL: funnel})
    submitted = {service.STATUS: 'En curso', service.PLANNED: '2026-11-04'}
    operational = patch(original, submitted)
    assert admission.approval_patch(original, submitted, operational, columns, confirmed=True) == operational
    with pytest.raises(ValueError, match='decisión'):
        admission.rejection_reason(original, 'Motivo', columns, confirmed=True)


def test_rejection_validates_reason_without_constructing_a_second_comments_format(columns):
    original = fields(**{service.COMMENTS: '<b>Anterior</b>'})
    before = deepcopy(original)
    assert admission.rejection_reason(original, '  Motivo\r\nOtra línea  ', columns, confirmed=True) == 'Motivo\nOtra línea'
    assert original == before


@pytest.mark.parametrize('reason', ['', '  \n ', None])
def test_rejection_requires_reason(columns, reason):
    with pytest.raises(ValueError, match='justificación'):
        admission.rejection_reason(fields(), reason, columns, confirmed=True)


def test_rejection_requires_explicit_confirmation(columns):
    with pytest.raises(ValueError, match='Confirmá'):
        admission.rejection_reason(fields(), 'Motivo', columns, confirmed=False)


@pytest.mark.parametrize('change', [
    {'readOnly': True}, {'readOnly': None}, {'calculated': {}},
    {'choice': {'choices': [admission.REQUESTED]}}, {'choice': None},
])
def test_unverified_or_incompatible_metadata_fails_closed(columns, change):
    columns[0].update(change)
    with pytest.raises(admission.AdmissionContractError):
        admission.validate_column(columns)


def test_missing_column_and_free_funnel_patch_are_rejected(columns):
    with pytest.raises(admission.AdmissionContractError):
        admission.validate_column([])
    with pytest.raises(ValueError, match='edición libre'):
        admission.approval_patch(fields(), {}, {service.FUNNEL: admission.APPROVED}, columns, confirmed=True)
