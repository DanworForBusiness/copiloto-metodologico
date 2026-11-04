"""Admission validation, separate from transport and operational status."""

from webapp.services import solicitudes_service as service
import logging

REQUESTED = "Solicitado"
APPROVED = "Aprobado para Arquitectura"
REJECTED = "Rechazado, Aplica Operaciones"
COMMITMENT_STATES = frozenset(("Planificado", "En curso", "Finalizado"))


def audit_approval(actor, item_id, correlation):
    """Only called after reread confirms the admission PATCH; no narrative data."""
    logging.getLogger('danwor.solicitudes.audit').info(
        'actor=%s item=%s operation=funnel_reconciliation transition="%s -> %s" correlation=%s',
        actor, item_id, REQUESTED, APPROVED, correlation,
    )


class AdmissionContractError(ValueError):
    pass


def validate_column(columns):
    """Require an explicitly writable choice admission catalog.

    Additional actual choices are retained, never treated as admission events.
    This check does not claim that a user has effective Microsoft write access.
    """
    definition = next((c for c in columns if c.get("name") == service.FUNNEL), {})
    choice = definition.get("choice")
    choices = choice.get("choices") if isinstance(choice, dict) else None
    if (not isinstance(choices, list) or not choices
            or any(not isinstance(value, str) or not value.strip() for value in choices)
            or len(set(choices)) != len(choices)
            or not {REQUESTED, APPROVED, REJECTED}.issubset(choices)
            or definition.get("readOnly") is not False
            or definition.get("calculated") is not None):
        raise AdmissionContractError(
            "No se pudo confirmar el catálogo editable de Status funnel. No se modificó la admisión."
        )
    return definition


def proposes_approval(fields, submitted, payload):
    """Reconcile only on explicit save, using the validated effective state.

    Payload is service.changes output. Submitted values alone cannot approve.
    Existing dates/states count, but removals/reversions in this save also count.
    """
    if fields.get(service.FUNNEL) != REQUESTED:
        return False
    effective = service.form_values({**fields, **payload})
    return (effective[service.STATUS] in COMMITMENT_STATES
            or bool(effective[service.PLANNED]) or bool(effective[service.ACTUAL]))


def approval_patch(fields, submitted, operational_patch, columns, *, confirmed):
    """Add admission to the SAME partial patch, only with explicit confirmation."""
    if service.FUNNEL in operational_patch:
        raise ValueError("Status funnel no admite edición libre.")
    result = dict(operational_patch)
    if proposes_approval(fields, submitted, operational_patch):
        validate_column(columns)
        if confirmed is not True:
            raise ValueError(
                "Esta solicitud tiene actividad comprometida por Arquitectura, pero su Funnel continúa en Solicitado. "
                "Al guardar, se actualizará a Aprobado para Arquitectura. ¿Continuar?"
            )
        result[service.FUNNEL] = APPROVED
    return result


def rejection_reason(fields, reason, columns, *, confirmed):
    """Validate admission; journal persistence supplies timestamp and identity."""
    validate_column(columns)
    if fields.get(service.FUNNEL) != REQUESTED:
        raise ValueError("La solicitud ya tiene una decisión de admisión. No se puede rechazar.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Ingresá una justificación para derivar la demanda a Operaciones.")
    if confirmed is not True:
        raise ValueError("Confirmá expresamente el rechazo de la demanda.")
    return service.normalize_narrative(reason.strip())
