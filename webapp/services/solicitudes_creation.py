"""Creation contract and one-use submission tickets for SharePoint requests."""
import logging
import hashlib
import json
import threading
import time
from uuid import uuid4
from urllib.error import HTTPError

from webapp.services import notifications
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service
from webapp.services import solicitudes_snapshot

CREATE_FIELDS = (
    "Title", "Prioridad", service.PROJECT, service.PLANNED, service.DESCRIPTION,
)
_lock = threading.Lock()
_tickets = {}
TICKET_TTL = 3600
TICKET_LIMIT = 1024
CREATED_EVENT = "operations.architecture_request.created"
DESCRIPTION_EXCERPT = 600


class SubmissionError(ValueError):
    pass


class UncertainCreation(RuntimeError):
    pass


def issue(actor):
    with _lock:
        now = time.monotonic()
        for key in [key for key, record in _tickets.items() if record["expires"] <= now]:
            del _tickets[key]
        if len(_tickets) >= TICKET_LIMIT:
            raise SubmissionError("No se pudo preparar el alta. Conservá tu borrador e intentá más tarde.")
        ticket = uuid4().hex
        _tickets[ticket] = {"actor": actor, "expires": now + TICKET_TTL, "state": "ready"}
        return ticket


def payload(submitted, options, columns):
    """Validate against current column definitions; let SharePoint supply defaults."""
    if set(submitted) - set(CREATE_FIELDS):
        raise ValueError("El alta contiene campos no permitidos.")
    submitted = {**submitted, service.STATUS: "Pendiente"}
    title = submitted.get("Title", "").strip()
    if not title:
        raise ValueError("Ingresá el título de la solicitud.")
    catalog = service.status_choices(columns)
    if submitted.get(service.STATUS) not in catalog:
        raise ValueError("Seleccioná un Status Arquitectura válido del catálogo de SharePoint.")
    options = {**options, service.STATUS: catalog}
    definitions = {column["name"]: column for column in columns}
    expected = {
        "Title": "text", service.STATUS: "choice", "Prioridad": "choice",
        service.PROJECT: "lookup", service.ASSIGNEES: "lookup",
        service.PLANNED: "dateTime", service.ACTUAL: "dateTime",
        service.DESCRIPTION: "text", service.COMMENTS: "text",
    }
    for name, kind in expected.items():
        definition = definitions.get(name, {})
        if kind not in definition or definition.get("readOnly") or "calculated" in definition:
            raise ValueError("El esquema de SharePoint no permite completar el alta con estos campos.")
        value = title if name == "Title" else submitted.get(name)
        if definition.get("required") and not value and not definition.get("defaultValue"):
            raise ValueError(f"Completá el campo obligatorio {definition.get('displayName', name)}.")
        if kind == "choice" and value and value not in definition[kind].get("choices", []):
            raise ValueError(f"El valor de {definition.get('displayName', name)} no está permitido en SharePoint.")
        if kind == "text" and value and len(value) > (definition[kind].get("maxLength") or float("inf")):
            raise ValueError(f"El texto de {definition.get('displayName', name)} supera el máximo permitido.")
    if definitions[service.PROJECT]["lookup"].get("allowMultipleValues", False):
        raise ValueError("Proyecto debe ser una referencia individual.")
    if not definitions[service.ASSIGNEES]["lookup"].get("allowMultipleValues", False):
        raise ValueError("La cardinalidad de Colaborador asignado cambió en SharePoint.")
    for name, definition in definitions.items():
        if (name not in expected and definition.get("required")
                and not definition.get("readOnly") and "calculated" not in definition
                and not definition.get("defaultValue")):
            raise ValueError("SharePoint requiere un campo adicional sin valor predeterminado. Revisá el contrato del alta antes de crear.")
    current = service.form_values({})
    supplied = {key: value for key, value in submitted.items() if key != "Title" and value}
    fields = service.changes(current, supplied, options)
    fields["Title"] = title
    return {"fields": fields}


def create(actor, ticket, token, submitted, *, origin=None):
    """Consume a server-issued ticket before POST; never replay an uncertain POST.

    Tickets belong to the process that issued them. After restart or on another
    worker they are rejected, rather than risking another item creation.
    """
    fingerprint = hashlib.sha256(json.dumps(submitted, sort_keys=True).encode()).hexdigest()
    with _lock:
        record = _tickets.get(ticket)
        if not record or record["actor"] != actor or record["expires"] <= time.monotonic():
            raise SubmissionError("El formulario venció. Conservá el borrador y abrí una nueva alta.")
        if record["state"] == "confirmed":
            if record["fingerprint"] != fingerprint:
                raise SubmissionError("Este formulario ya creó una solicitud con otros valores.")
            return record["item_id"]
        if record["state"] != "ready":
            raise SubmissionError("Este envío ya está en proceso o requiere revisar su resultado. No vuelvas a crear la misma solicitud.")
        record["state"] = "validating"
    try:
        columns = repository.read_creation_columns(token)
        options = repository.lookup_options(token, columns=columns)
        body = payload(submitted, options, columns)
    except Exception:
        with _lock:
            record["state"] = "ready"
        raise
    with _lock:
        record["state"] = "posting"
        record["fingerprint"] = fingerprint
    outcome = "uncertain"
    try:
        result = repository.create_item(token, body)
        item_id = str(result.get("id", ""))
        if not item_id.isascii() or not item_id.isdecimal():
            raise UncertainCreation()
        with _lock:
            record["item_id"] = item_id
        observed = repository.read_item(token, item_id)
        actual = service.form_values(observed["fields"])
        if (str(observed.get("id")) != item_id
                or actual[service.STATUS] != "Pendiente"
                or observed["fields"].get("Title") != body["fields"]["Title"]
                or service.changes(actual, {key: value for key, value in submitted.items() if key != "Title" and value}, options)):
            raise UncertainCreation()
        with _lock:
            record["state"] = "confirmed"
        outcome = "confirmed"
        _announce_created(actor, item_id, observed, options, origin)
        return item_id
    except HTTPError as exc:
        with _lock:
            record["state"] = "ready" if exc.code < 500 and "item_id" not in record else "uncertain"
        if record["state"] == "ready":
            outcome = f"http_{exc.code}"
            raise
        raise UncertainCreation() from exc
    except Exception as exc:
        with _lock:
            record["state"] = "uncertain"
        raise UncertainCreation() from exc
    finally:
        solicitudes_snapshot.invalidate(actor)
        logging.getLogger("danwor.solicitudes.audit").info(
            "actor=%s operation=create outcome=%s correlation=%s", actor, outcome, ticket,
        )


def _announce_created(actor, item_id, observed, options, origin):
    """Evento de negocio `operations.architecture_request.created`: SOLO tras la confirmación durable en SharePoint (la relectura coincidió) y
    solo en la PRIMERA confirmación del ticket (un reenvío del mismo formulario devuelve el id antes de llegar acá). Operaciones solo describe
    la solicitud con sus datos reales; el envío, los destinatarios y los reintentos son del servicio de notificaciones. Nunca afecta el alta."""
    if not notifications.enabled():                                # apagadas: el alta se comporta exactamente como siempre (ni siquiera se arman datos)
        return
    try:
        fields = observed.get("fields") or {}
        description = notifications.readable_text(fields.get(service.DESCRIPTION), DESCRIPTION_EXCERPT)   # texto legible: párrafos y saltos conservados
        notifications.emit(CREATED_EVENT, item_id, {
            "id": item_id,
            "title": str(fields.get("Title") or ""),
            "project": service.project_label(fields, (options or {}).get(service.PROJECT)),
            "priority": str(fields.get("Prioridad") or ""),
            "status": str(fields.get(service.STATUS) or ""),
            "requester": str(actor or ""),
            "created": _business_time(observed.get("createdDateTime")),
            "description": description,
            "url": notifications.hub_link(f"/operaciones/solicitudes/{item_id}/editar", origin),
        }, origin=origin)
    except Exception as exc:                                      # noqa: BLE001 — la notificación es accesoria: el alta ya está confirmada
        logging.getLogger("danwor.solicitudes.audit").warning("operation=create notification=not_emitted item=%s error=%s", item_id, type(exc).__name__)


def _business_time(value):
    from datetime import datetime
    from webapp.services.busqueda_repository import BUSINESS_TZ
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return moment.astimezone(BUSINESS_TZ).strftime("%d/%m/%Y %H:%M")
