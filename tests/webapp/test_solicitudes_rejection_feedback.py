"""Real rejection/notification chain, with SharePoint, OAuth and Graph simulated."""
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError

import pytest

import config
from webapp.services import notifications, solicitudes_admission as admission
from webapp.services import solicitudes_repository as repository, solicitudes_service as service
from tests.webapp.test_notifications import graph, SECRET, TOKEN  # noqa: F401
from tests.webapp.test_solicitudes_journal import columns, setup, send, USER  # noqa: F401


@pytest.fixture(autouse=True)
def authorized_actor(monkeypatch):
    monkeypatch.setattr(repository, "edit_permissions", lambda *args: (True, True))


@pytest.mark.parametrize("transport,state,phrase,http", [
    (202, "accepted", "La entrega al destinatario no está acreditada", 202),
    (HTTPError("", 403, "Fixture", {}, None), "failed", "La notificación falló", 403),
    (TimeoutError(), "uncertain", "El correo podría haberse aceptado", None),
    (HTTPError("", 500, "Fixture", {}, None), "uncertain", "El correo podría haberse aceptado", 500),
])
def test_confirmed_rejection_reports_transport_without_rolling_back(setup, graph, caplog, transport, state, phrase, http):
    graph.responses = [transport]
    with caplog.at_level("INFO", logger="danwor.solicitudes.audit"):
        response = send(setup, path="rechazar", reason="Motivo privado de demostración", confirm_rejection="yes")
    assert response.status_code == 200
    assert 'data-solicitudes-saved' in response.text
    assert f'data-solicitudes-notification="{state}"' in response.text
    assert "Rechazo confirmado en SharePoint" in response.text and phrase in response.text
    assert setup[1]["fields"][service.FUNNEL] == admission.REJECTED
    assert len(setup[2]) == 1 and len(graph.calls) == 1
    assert "outcome=confirmed" in caplog.text and f"notification={state}" in caplog.text
    assert f"http_status={http}" in caplog.text and "correlation=" in caplog.text
    assert not any(value in caplog.text for value in [SECRET, TOKEN, "fixture-secret", "Motivo privado de demostración"])
    refreshed = setup[0].post("/operaciones/solicitudes/1/detalle", data={"graph_token": "fixture-secret"})
    assert admission.REJECTED in refreshed.text and "Motivo privado de demostración" in refreshed.text
    replay = send(setup, path="rechazar", reason="No duplicar", confirm_rejection="yes", etag='"8"')
    assert 'data-solicitudes-notification="not_attempted"' in replay.text
    assert len(setup[2]) == len(graph.calls) == 1
    notifications.resume_pending()
    assert len(graph.calls) == 1  # accepted, failed and uncertain are terminal


@pytest.mark.parametrize("mode,state,phrase", [
    ("recipients", "no_recipients", "no hay destinatarios"),
    ("disabled", "disabled", "deshabilitados"),
    ("config", "config_incomplete", "configuración incompleta"),
    ("pending", "pending", "todavía no está verificada"),
    ("registry_error", "unknown", "no verificable"),
])
def test_omitted_pending_or_unreadable_delivery_keeps_confirmed_rejection(setup, graph, monkeypatch, mode, state, phrase):
    if mode == "recipients":
        monkeypatch.setattr(config, "NOTIFICATIONS_OPERATIONS_RECIPIENTS", "")
    elif mode == "disabled":
        monkeypatch.setattr(config, "NOTIFICATIONS_ENABLED", False)
    elif mode == "config":
        monkeypatch.setattr(config, "NOTIFICATIONS_CLIENT_SECRET", "")
    elif mode == "pending":
        class PausedThread:
            def __init__(self, **kwargs): pass
            def start(self): pass
        monkeypatch.setattr(notifications.threading, "Thread", PausedThread)
    else:
        monkeypatch.setattr(notifications, "delivery_status", lambda *a: (_ for _ in ()).throw(OSError("Fixture")))
    response = send(setup, path="rechazar", reason="Motivo", confirm_rejection="yes")
    assert "Rechazo confirmado en SharePoint" in response.text
    assert f'data-solicitudes-notification="{state}"' in response.text and phrase in response.text
    assert setup[1]["fields"][service.FUNNEL] == admission.REJECTED and len(setup[2]) == 1
    assert len(graph.calls) == (1 if mode == "registry_error" else 0)


@pytest.mark.parametrize("mode", ["unauthorized", "invalid_transition", "unconfirmed_write"])
def test_unconfirmed_or_forbidden_rejection_never_notifies(setup, graph, monkeypatch, mode):
    if mode == "unauthorized":
        monkeypatch.setattr(repository, "edit_permissions", lambda *a: (True, False))
    elif mode == "invalid_transition":
        setup[1]["fields"][service.FUNNEL] = admission.APPROVED
    else:
        monkeypatch.setattr(repository, "update_item", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    response = send(setup, path="rechazar", reason="Conservar", confirm_rejection="yes")
    assert 'data-solicitudes-saved' not in response.text and graph.calls == []
    assert not list(Path(config.NOTIFICATIONS_DIR).rglob("*.json"))
    if mode == "unauthorized":
        assert response.status_code == 403
    else:
        assert 'data-solicitudes-notification="not_attempted"' in response.text


def test_duplicate_notification_reads_record_without_resending(graph):
    args = ("fake", [], "1", "entry", {"fields": {}}, "Motivo", USER)
    first = admission.announce_rejection(*args)
    second = admission.announce_rejection(*args)
    assert first["dispatch"] == "queued" and first["state"] == "accepted"
    assert second["dispatch"] == "duplicate" and second["state"] == "accepted"
    assert len(graph.calls) == 1


def test_delivery_snapshot_excludes_sensitive_record_fields(graph):
    path = notifications.record_path(admission.REJECTED_EVENT, "safe")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": "failed", "error": "Graph HTTP 403", "data": {"reason": "PRIVATE"}, "token": TOKEN}), encoding="utf-8")
    assert notifications.delivery_status(admission.REJECTED_EVENT, "safe") == {"state": "failed", "category": "http", "http_status": 403}
    path.write_text("invalid json", encoding="utf-8")
    assert notifications.delivery_status(admission.REJECTED_EVENT, "safe")["state"] == "unknown"


def test_rejection_client_feedback_contract():
    script = Path(__file__).with_name("solicitudes_rejection_feedback.node.js")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
