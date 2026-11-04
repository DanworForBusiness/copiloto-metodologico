"""Admission validation, separate from transport and operational status."""

from webapp.services import solicitudes_service as service

REQUESTED = "Solicitado"
APPROVED = "Aprobado para Arquitectura"
REJECTED = "Rechazado, Aplica Operaciones"
COMMITMENT_STATES = frozenset(("Planificado", "En curso", "Finalizado"))


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
    """Detect an actual first date or changed commitment state, after validation.

    `payload` must be the result of service.changes, not untrusted form data.
    No repair/backfill occurs for commitments that were already present.
    """
    if fields.get(service.FUNNEL) != REQUESTED:
        return False
    current = service.form_values(fields)
    first_date = any(
        not current[key] and bool(submitted.get(key)) and key in payload
        for key in (service.PLANNED, service.ACTUAL)
    )
    changed_status = (
        service.STATUS in payload
        and submitted.get(service.STATUS) in COMMITMENT_STATES
        and submitted[service.STATUS] != current[service.STATUS]
    )
    return first_date or changed_status


def approval_patch(fields, submitted, operational_patch, columns, *, confirmed):
    """Add admission to the SAME partial patch, only with explicit confirmation."""
    if service.FUNNEL in operational_patch:
        raise ValueError("Status funnel no admite edición libre.")
    result = dict(operational_patch)
    if proposes_approval(fields, submitted, operational_patch):
        validate_column(columns)
        if confirmed is not True:
            raise ValueError(
                "Al confirmar estos cambios, la solicitud pasará a Aprobado para Arquitectura. ¿Continuar?"
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
