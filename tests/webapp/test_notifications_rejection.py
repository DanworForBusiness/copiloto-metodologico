"""
test_notifications_rejection.py — segundo evento de workflow (`operations.architecture_request.rejected`) y política To/Cc por responsabilidad.

Rechazo / Derivación a Operaciones: Arquitectura rechaza la admisión (Status funnel «Solicitado» → «Rechazado, Aplica Operaciones»); actúa
Operaciones exclusivamente (To), sin copia automática a Arquitectura. Graph y SharePoint SIMULADOS (fixtures de test_notifications y test_solicitudes_journal).
"""
from __future__ import annotations

import json

import pytest

import config
from webapp.services import notifications
from webapp.services import solicitudes_admission as admission
from webapp.services import solicitudes_journal as journal
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service
from webapp import access
from tests.webapp.test_notifications import _http, graph  # noqa: F401 — fixture
from tests.webapp.test_solicitudes_creation import catalogs, client, schema, submitted  # noqa: F401 — fixtures que `graph` arrastra
from tests.webapp.test_solicitudes_journal import USER, columns, send, setup  # noqa: F401 — fixtures

EVENT = "operations.architecture_request.rejected"
REASON = "No corresponde a Arquitectura: es una consulta de soporte operativo.\nDerivar a la mesa de Operaciones."


@pytest.fixture
def workflow(setup, graph, monkeypatch):  # noqa: F811
    """Solicitud #1 en «Solicitado» con título, proyecto (lookup) y fecha de modificación confirmados por la relectura."""
    # Exercise the real edit policy with an authenticated APP_OWNER actor;
    # importing setup does not import its module-local authorization fixture.
    monkeypatch.setitem(USER, "sub", USER["email"])
    monkeypatch.setattr(access, "APP_OWNER", USER["email"])
    current = setup[1]
    current["fields"].update({"Title": "Modelo de stock", service.PROJECT + "LookupId": "4"})
    current["lastModifiedDateTime"] = "2026-11-10T18:05:00Z"
    monkeypatch.setattr(repository, "lookup_options", lambda *a, **k: {
        service.STATUS: ["Pendiente", "En curso"], service.PROJECT: {"4": "Proyecto Kensing"}, service.ASSIGNEES: {}})
    return setup


def _reject(setup, **changes):  # noqa: F811
    return send(setup, path="rechazar", **{"reason": REASON, "confirm_rejection": "yes", **changes})


def _records(event=EVENT):
    folder = notifications.record_path(event, "x").parent
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def _addresses(message, field):
    return [r["emailAddress"]["address"] for r in message.get(field, [])]


# ── Rechazo confirmado → solo To Operaciones, contenido confirmado, enlace al detalle ─────────────────────────────────────────────────────
def test_confirmed_rejection_notifies_only_operations(workflow, graph):  # noqa: F811
    response = _reject(workflow)
    assert "data-solicitudes-saved" in response.text                                    # el rechazo de negocio se confirmó
    assert len(graph.calls) == 1
    message = graph.calls[0]["body"]["message"]
    assert _addresses(message, "toRecipients") == ["operaciones@example.com"]
    assert not _addresses(message, "ccRecipients") and not _addresses(message, "bccRecipients")
    assert message["subject"] == "Solicitud rechazada y derivada a Operaciones | Arquitectura | Solicitud #1"
    html = message["body"]["content"]
    for text in ("Solicitud rechazada y derivada a Operaciones", "#1", "Modelo de stock", "Proyecto Kensing", admission.REJECTED,
                 USER["email"], "10/11/2026 15:05", "Motivo del rechazo", "No corresponde a Arquitectura", "Derivar a la mesa de Operaciones"):
        assert text in html, text
    assert 'href="http://testserver/operaciones/solicitudes/1/editar"' in html
    (record,) = _records()
    assert record["state"] == "accepted" and record["key"].startswith("1:")
    assert "fixture-secret" not in json.dumps(record)                                     # el token delegado del usuario nunca se registra


def test_rejection_reason_is_rendered_as_safe_text(workflow, graph):  # noqa: F811
    _reject(workflow, reason="Motivo <script>alert(1)</script> & <b>negrita</b>")
    html = graph.calls[0]["body"]["message"]["body"]["content"]
    assert "<script>" not in html and "<b>negrita" not in html
    assert "Motivo" in html and "&amp;" in html


# ── No es rechazo: diálogo cancelado, validación, concurrencia, decisión previa, edición común, resultado incierto ──────────────────────────
def test_cancelled_dialog_and_plain_detail_refresh_send_nothing(workflow, graph):  # noqa: F811
    # Cancelar el diálogo no emite request alguno; abrir/refrescar el detalle es lectura.
    for _ in range(2):
        assert workflow[0].post("/operaciones/solicitudes/1/detalle", data={"graph_token": "fixture"}).status_code == 200
    assert workflow[0].get("/operaciones/solicitudes/1/editar").status_code in (200, 302, 303, 307)
    assert not graph.calls and not _records()


@pytest.mark.parametrize("changes", [{"reason": ""}, {"confirm_rejection": ""}, {"Prioridad": "Alta"}])
def test_validation_errors_send_nothing(workflow, graph, changes):  # noqa: F811
    response = _reject(workflow, **changes)
    assert "data-solicitudes-saved" not in response.text and not workflow[2]
    assert not graph.calls and not _records()


def test_stale_revision_and_concurrent_patch_send_nothing(workflow, graph, monkeypatch):  # noqa: F811
    assert "data-solicitudes-saved" not in _reject(workflow, etag='"6"').text          # revisión vencida: ni siquiera se intenta escribir

    def precondition_failed(*args):
        raise _http(412)
    monkeypatch.setattr(repository, "update_item", precondition_failed)
    assert "data-solicitudes-saved" not in _reject(workflow).text                       # otro usuario escribió primero (412)
    assert not graph.calls and not _records()


@pytest.mark.parametrize("decision", [admission.APPROVED, admission.REJECTED])
def test_existing_admission_decision_sends_nothing(workflow, graph, decision):  # noqa: F811
    workflow[1]["fields"][service.FUNNEL] = decision
    assert "data-solicitudes-saved" not in _reject(workflow).text
    assert not graph.calls


def test_ordinary_edit_is_not_a_rejection(workflow, graph):  # noqa: F811
    response = send(workflow, path="guardar", new_observation="Seguimos analizando")
    assert "data-solicitudes-saved" in response.text
    assert not graph.calls and not _records() and not _records("operations.architecture_request.created")


def test_unconfirmed_rejection_sends_nothing(workflow, graph, monkeypatch):  # noqa: F811
    def lost(token, item_id, payload, etag):
        raise TimeoutError()                                                            # el PATCH no llegó y la relectura no lo confirma
    monkeypatch.setattr(repository, "update_item", lost)
    assert "data-solicitudes-review" in _reject(workflow).text
    assert not graph.calls and not _records()


# ── Idempotencia: misma transición vs. rechazo posterior legítimo ───────────────────────────────────────────────────────────────────────────
def test_retrying_the_same_transition_never_sends_twice(workflow, graph):  # noqa: F811
    ticket = journal.issue("1", USER["sub"])
    assert "data-solicitudes-saved" in _reject(workflow, observation_ticket=ticket).text
    replay = _reject(workflow, observation_ticket=ticket, etag='"8"')                    # mismo formulario reenviado / reintento del usuario
    assert "data-solicitudes-saved" not in replay.text
    (record,) = _records()
    assert notifications.emit(EVENT, record["key"], record["data"]) == "duplicate"       # aun si un productor reintentara, la clave lo frena
    assert len(graph.calls) == 1


def test_a_later_legitimate_rejection_has_its_own_key(workflow, graph):  # noqa: F811
    _reject(workflow)
    workflow[1]["fields"][service.FUNNEL] = admission.REQUESTED                           # la demanda volvió a «Solicitado» (nueva transición)
    assert "data-solicitudes-saved" in _reject(workflow, etag='"8"', reason="Sigue sin aplicar.").text
    keys = [r["key"] for r in _records()]
    assert len(graph.calls) == 2 and len(set(keys)) == 2 and all(k.startswith("1:") for k in keys)
    assert "Sigue sin aplicar." in graph.calls[1]["body"]["message"]["body"]["content"]


# ── Global, fallos de Graph y política To/Cc ────────────────────────────────────────────────────────────────────────────────────────────────
def test_disabled_notifications_leave_the_rejection_untouched(workflow, graph, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_ENABLED", False)
    assert "data-solicitudes-saved" in _reject(workflow).text
    assert not graph.calls and not _records()


@pytest.mark.parametrize("failure", [400, 500])
def test_graph_failure_never_reverts_the_confirmed_rejection(workflow, graph, failure):  # noqa: F811
    graph.responses = [_http(failure)]
    assert "data-solicitudes-saved" in _reject(workflow).text
    assert workflow[1]["fields"][service.FUNNEL] == admission.REJECTED and len(workflow[2]) == 1
    (record,) = _records()
    assert record["state"] == ("failed" if failure == 400 else "uncertain")


def test_multiple_addresses_are_normalized_without_cross_group_copy(monkeypatch):
    monkeypatch.setattr(config, "NOTIFICATIONS_OPERATIONS_RECIPIENTS", " ops1@example.com ;OPS2@example.com, ops1@example.com,no-es-correo ")
    monkeypatch.setattr(config, "NOTIFICATIONS_ARQ_RECIPIENTS", "arq@example.com; ops2@example.com")
    to, cc = notifications.recipients_for(notifications.EVENTS[EVENT])
    assert to == ["ops1@example.com", "OPS2@example.com"] and cc == []
    to, cc = notifications.recipients_for(notifications.EVENTS["operations.architecture_request.created"])
    assert to == ["arq@example.com", "ops2@example.com"] and cc == []
    message = notifications.build_message(EVENT, {"id": "1"}, ["a@example.com"])["message"]
    assert "ccRecipients" not in message                                                 # sin copia configurada, sin campo Cc


def test_missing_primary_recipients_never_send_to_the_copy_alone(workflow, graph, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_OPERATIONS_RECIPIENTS", "")
    assert "data-solicitudes-saved" in _reject(workflow).text
    assert not graph.calls
    (record,) = _records()
    assert record["state"] == "failed" and record["error"] == "no_recipients"
    assert "NOTIFICATIONS_OPERATIONS_RECIPIENTS" in caplog.text


def test_missing_copy_sends_only_to_the_primary(workflow, graph, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_ARQ_RECIPIENTS", "")
    _reject(workflow)
    message = graph.calls[0]["body"]["message"]
    assert _addresses(message, "toRecipients") == ["operaciones@example.com"] and "ccRecipients" not in message
