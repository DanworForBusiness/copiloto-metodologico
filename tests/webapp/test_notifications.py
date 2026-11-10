"""
test_notifications.py — MVP de notificaciones por correo: Operaciones → 03. Solicitudes, evento `operations.architecture_request.created`.

Graph y el endpoint de token están SIMULADOS (nunca hay envíos reales). El envío en segundo plano se ejecuta en línea para que las pruebas sean
deterministas, y las esperas entre reintentos se anulan.
"""
from __future__ import annotations

import json
import logging
import socket
import urllib.error

import pytest

import config
from core import microsoft365, microsoft365_app
from webapp.services import notifications
from webapp.services import solicitudes_creation as creation
from webapp.services import solicitudes_repository as repository
from webapp.services import solicitudes_service as service
from tests.webapp.test_solicitudes_creation import catalogs, client, schema, submitted  # noqa: F401 — fixtures

SECRET = "s3cret-VALUE-no-debe-aparecer"
TOKEN = "eyJ-app-token-no-debe-aparecer"
ORIGIN = {"Origin": "http://testserver"}


class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


@pytest.fixture
def graph(monkeypatch, tmp_path):
    """Notificaciones habilitadas con configuración completa + Graph simulado. `graph.responses` define la respuesta de cada intento."""
    for name, value in {"NOTIFICATIONS_ENABLED": True, "NOTIFICATIONS_TENANT_ID": "tenant-x", "NOTIFICATIONS_CLIENT_ID": "client-x",
                        "NOTIFICATIONS_CLIENT_SECRET": SECRET, "NOTIFICATIONS_SENDER": "hub-notifications@danwor.com",
                        "NOTIFICATIONS_OPERATIONS_RECIPIENTS": "arquitectura@danwor.com; operaciones@danwor.com",
                        "NOTIFICATIONS_HUB_BASE_URL": "", "NOTIFICATIONS_DIR": tmp_path / "notifications",
                        "ALLOWED_USERS": "intruso@danwor.com", "APP_OWNER": "owner@danwor.com"}.items():
        monkeypatch.setattr(config, name, value, raising=False)
    monkeypatch.setattr(notifications.threading, "Thread", _InlineThread)
    monkeypatch.setattr(notifications.time, "sleep", lambda s: None)

    class Graph:
        responses: list = []
        calls: list = []
        tokens: list = []

    def token(tenant, client_id, secret, **_):
        Graph.tokens.append((tenant, client_id, secret == SECRET))
        return TOKEN

    def post(url, access_token, body, **_):
        Graph.calls.append({"url": url, "token_ok": access_token == TOKEN, "body": body})
        outcome = Graph.responses.pop(0) if Graph.responses else 202
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    Graph.responses, Graph.calls, Graph.tokens = [], [], []
    monkeypatch.setattr(microsoft365_app, "acquire_app_token", token)
    monkeypatch.setattr(microsoft365, "graph_post_accepted", post)
    return Graph


@pytest.fixture
def sharepoint(monkeypatch, submitted):  # noqa: F811
    created = []
    fields = {**submitted, service.PROJECT + "LookupId": "4", "Prioridad": "Alta",
              service.DESCRIPTION: "<div>Necesitamos <b>modelar</b> &amp; publicar</div>"}
    monkeypatch.setattr(repository, "create_item", lambda token, body: created.append(body) or {"id": "7"})
    monkeypatch.setattr(repository, "read_item", lambda token, item_id: {"id": "7", "createdDateTime": "2026-11-10T15:30:00Z", "fields": fields})
    return created


def _create(client, submitted):  # noqa: F811
    ticket = creation.issue("create-test")
    return client.post("/operaciones/solicitudes/nueva", data={**submitted, "graph_token": "user-token", "submission_id": ticket}, headers=ORIGIN), ticket


def _record(event="operations.architecture_request.created", key="7"):
    return json.loads(notifications.record_path(event, key).read_text(encoding="utf-8"))


def _http(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://graph.microsoft.com/x", code, "x", headers, None)


# 1–6 · alta confirmada → una intención, remitente/destinatarios gobernados, asunto/contenido y enlace
def test_confirmed_creation_sends_one_governed_notification(client, submitted, graph, sharepoint):  # noqa: F811
    response, _ = _create(client, submitted)
    assert "Solicitud creada y confirmada en SharePoint." in response.text
    assert len(graph.calls) == 1 and graph.calls[0]["token_ok"]
    assert graph.tokens == [("tenant-x", "client-x", True)]                                           # identidad de aplicación (no el token del usuario)
    call = graph.calls[0]
    assert call["url"] == "https://graph.microsoft.com/v1.0/users/hub-notifications@danwor.com/sendMail"
    message = call["body"]["message"]
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == ["arquitectura@danwor.com", "operaciones@danwor.com"]
    assert message["subject"] == "Nueva solicitud de arquitectura | Operaciones | Solicitud #7"
    html = message["body"]["content"]
    assert message["body"]["contentType"] == "HTML"
    for text in ("Solicitud simulada", "Proyecto simulado", "Alta", "Pendiente", "create-test", "10/11/2026 12:30", "Necesitamos modelar &amp; publicar"):
        assert text in html
    assert 'href="http://testserver/operaciones/solicitudes/7/editar"' in html
    assert "<b>" not in html                                                                          # la descripción llega como texto plano
    record = _record()
    assert record["state"] == "accepted" and record["attempts"] == 1 and SECRET not in json.dumps(record) and TOKEN not in json.dumps(record)


def test_recipients_never_come_from_access_lists_or_the_frontend(client, submitted, graph, sharepoint):  # noqa: F811
    _create(client, {**submitted})
    addresses = json.dumps(graph.calls[0]["body"])
    assert "intruso@danwor.com" not in addresses and "owner@danwor.com" not in addresses and "create-test" not in json.dumps(graph.calls[0]["body"]["message"]["toRecipients"])


def test_configured_base_url_wins_for_the_link(client, submitted, graph, sharepoint, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_HUB_BASE_URL", "https://hub.danwor.com")
    _create(client, submitted)
    assert 'href="https://hub.danwor.com/operaciones/solicitudes/7/editar"' in graph.calls[0]["body"]["message"]["body"]["content"]


def test_dynamic_values_are_escaped():
    html = notifications.build_message("operations.architecture_request.created", {"id": "9", "title": "<script>x</script>", "url": "/x"}, ["a@danwor.com"])
    assert "<script>x</script>" not in html["message"]["body"]["content"] and "&lt;script&gt;" in html["message"]["body"]["content"]


# 7–8 · feature flag y configuración incompleta
def test_disabled_flag_sends_nothing_and_creation_is_unchanged(client, submitted, graph, sharepoint, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_ENABLED", False)
    response, _ = _create(client, submitted)
    assert "Solicitud creada y confirmada en SharePoint." in response.text
    assert graph.calls == [] and not notifications.record_path("operations.architecture_request.created", "7").exists()


def test_missing_secret_is_a_clear_configuration_error_without_values(client, submitted, graph, sharepoint, monkeypatch, caplog):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_CLIENT_SECRET", "")
    with caplog.at_level(logging.INFO, logger="danwor.notifications"):
        response, _ = _create(client, submitted)
        notifications.startup_check()
    assert "Solicitud creada y confirmada en SharePoint." in response.text and graph.calls == []
    assert _record()["state"] == "failed" and _record()["error"] == "config_incomplete"
    assert "NOTIFICATIONS_CLIENT_SECRET" in caplog.text and "tenant-x" not in caplog.text


# 9–11 · errores Graph: transitorio, definitivo, incierto; el alta se mantiene
def test_transient_graph_error_is_retried_and_accepted(client, submitted, graph, sharepoint):  # noqa: F811
    graph.responses = [_http(503, retry_after=1), 202]
    response, _ = _create(client, submitted)
    assert "Solicitud creada y confirmada en SharePoint." in response.text
    assert len(graph.calls) == 2 and _record()["state"] == "accepted" and _record()["attempts"] == 2


def test_exhausted_transient_errors_stay_resumable_and_resume_later(client, submitted, graph, sharepoint):  # noqa: F811
    graph.responses = [_http(429), _http(503), urllib.error.URLError(ConnectionRefusedError())]
    _create(client, submitted)
    assert _record()["state"] == "retry" and _record()["attempts"] == 3
    graph.responses = [202]
    assert notifications.resume_pending() == 1
    assert _record()["state"] == "accepted" and len(graph.calls) == 4


def test_definitive_graph_error_is_not_retried_and_creation_stays(client, submitted, graph, sharepoint):  # noqa: F811
    graph.responses = [_http(403)]
    response, _ = _create(client, submitted)
    assert "Solicitud creada y confirmada en SharePoint." in response.text and len(sharepoint) == 1
    assert _record()["state"] == "failed" and _record()["error"] == "Graph HTTP 403"
    assert notifications.resume_pending() == 0 and len(graph.calls) == 1


def test_uncertain_outcome_is_never_resent_automatically(client, submitted, graph, sharepoint):  # noqa: F811
    graph.responses = [socket.timeout()]
    _create(client, submitted)
    assert _record()["state"] == "uncertain"
    assert notifications.resume_pending() == 0 and len(graph.calls) == 1


def test_token_endpoint_failure_does_not_send_and_is_classified(client, submitted, graph, sharepoint, monkeypatch):  # noqa: F811
    def bad_token(*a, **k):
        raise microsoft365_app.AppTokenError("el endpoint de token respondió HTTP 401", transient=False, status=401)
    monkeypatch.setattr(microsoft365_app, "acquire_app_token", bad_token)
    response, _ = _create(client, submitted)
    assert "Solicitud creada y confirmada en SharePoint." in response.text and graph.calls == []
    assert _record()["state"] == "failed" and "401" in _record()["error"]


# 12–13 · sin reenvíos por refresh/GET/doble submit/reprocesamiento/reinicio
def test_double_submit_refresh_and_reprocessing_never_duplicate(client, submitted, graph, sharepoint):  # noqa: F811
    ticket = creation.issue("create-test")
    data = {**submitted, "graph_token": "user-token", "submission_id": ticket}
    for _ in range(3):                                                                                 # doble submit / reintento HTTP del mismo formulario
        client.post("/operaciones/solicitudes/nueva", data=data, headers=ORIGIN)
    client.get("/operaciones/solicitudes")                                                             # navegación / refresh
    client.get("/operaciones/solicitudes/nueva")
    assert len(sharepoint) == 1 and len(graph.calls) == 1
    assert notifications.emit("operations.architecture_request.created", "7", {"id": "7"}) == "duplicate"   # reprocesamiento del mismo evento
    assert len(graph.calls) == 1


def test_restart_resumes_pending_but_never_resends_an_interrupted_send(graph):
    pending = notifications.record_path("operations.architecture_request.created", "11")
    sending = notifications.record_path("operations.architecture_request.created", "12")
    pending.parent.mkdir(parents=True, exist_ok=True)
    base = {"event": "operations.architecture_request.created", "data": {"id": "x"}, "attempts": 0}
    pending.write_text(json.dumps({**base, "key": "11", "state": "pending"}), encoding="utf-8")
    sending.write_text(json.dumps({**base, "key": "12", "state": "sending", "attempts": 1}), encoding="utf-8")
    assert notifications.resume_pending() == 1
    assert _record(key="11")["state"] == "accepted" and _record(key="12")["state"] == "uncertain" and len(graph.calls) == 1


# 14 · sin credenciales en logs
def test_no_credentials_or_tokens_in_logs(client, submitted, graph, sharepoint, caplog):  # noqa: F811
    graph.responses = [_http(503), _http(401)]
    with caplog.at_level(logging.DEBUG):
        _create(client, submitted)
    assert SECRET not in caplog.text and TOKEN not in caplog.text and "user-token" not in caplog.text


def test_unregistered_events_and_internal_errors_never_raise(graph):
    assert notifications.emit("operations.unknown", "1", {}) == "error"


# Transporte y token (sin red real)
class _Resp:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b""


def test_graph_post_accepted_returns_the_status_without_decoding_an_empty_body(monkeypatch):
    seen = {}

    def urlopen(request, timeout=None):
        seen.update(method=request.get_method(), auth=request.headers.get("Authorization"), ctype=request.headers.get("Content-type"))
        return _Resp()
    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", urlopen)
    assert microsoft365.graph_post_accepted("https://graph.microsoft.com/v1.0/users/x/sendMail", "tkn", {"message": {}}) == 202
    assert seen == {"method": "POST", "auth": "Bearer tkn", "ctype": "application/json"}


@pytest.mark.parametrize("code,transient", [(400, False), (401, False), (429, True), (503, True)])
def test_app_token_errors_are_classified_and_never_echo_the_secret(monkeypatch, code, transient):
    def urlopen(request, timeout=None):
        assert b"grant_type=client_credentials" in request.data and b"scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in request.data
        raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
    monkeypatch.setattr(microsoft365_app.urllib.request, "urlopen", urlopen)
    with pytest.raises(microsoft365_app.AppTokenError) as info:
        microsoft365_app.acquire_app_token("tenant", "client", SECRET)
    assert info.value.transient is transient and SECRET not in str(info.value)


# Identidad visual: institucional (logo) → vertical (workspaces) → evento → datos
def _render(event="operations.architecture_request.created", data=None, base="https://hub.danwor.com"):
    return notifications.build_message(event, data or {"id": "7", "title": "T", "url": "/x"}, ["a@danwor.com"], hub_base=base)["message"]["body"]["content"]


def test_header_uses_the_shared_logo_and_the_vertical_identity_from_the_hub_registry():
    from webapp import workspaces
    html = _render()
    ident = workspaces.brand_identity("operations")
    assert 'src="https://hub.danwor.com/static/brand/danwor-logo.png"' in html and 'alt="Danwor"' in html   # HTTPS público del propio Hub
    assert ident["title"] in html and ident["accent"] in html and ident["title"] == workspaces.WORKSPACE_CARD_COPY["operations"]["title"]
    card = workspaces.WORKSPACE_CARD_COPY["operations"]
    assert card["description"] in html and all(feature in html for feature in card["features"])          # hero = la tarjeta de la vertical
    assert html.index(card["features"][-1]) < html.index("Nueva solicitud de arquitectura</h1>")          # el hero termina antes del contenido del evento


def test_without_an_absolute_base_the_logo_is_omitted_and_the_header_stays_readable():
    html = _render(base="")
    assert "<img" not in html and ">Danwor<" in html                                                   # nunca una ruta relativa ni localhost implícito


def test_shared_layout_knows_no_vertical_by_name():
    from pathlib import Path
    layout = (Path(__file__).resolve().parents[2] / "webapp/templates/notifications/_layout.html").read_text(encoding="utf-8")
    for name in ("Operaciones", "Gestión Inteligente", "Capital Humano", "CRM", "Comercial"):
        assert name not in layout


@pytest.mark.parametrize("key", ["commercial", "human-capital", "copilot"])
def test_the_same_header_renders_another_vertical_from_its_existing_metadata(monkeypatch, key):
    from webapp import workspaces
    spec = notifications.EVENTS["operations.architecture_request.created"]
    monkeypatch.setitem(notifications.EVENTS, "demo.other_vertical", notifications.EventSpec(
        workspace=key, recipients_setting=spec.recipients_setting, subject=spec.subject, template=spec.template, title=spec.title))
    html = _render("demo.other_vertical")
    ident = workspaces.brand_identity(key)
    assert ident["title"] in html and ident["accent"] in html and "Gestión Inteligente" not in html


@pytest.mark.parametrize("raw,expected", [
    ("<div><p>Necesitamos <b>diseñar</b> el modelo &amp; la capa.</p><p>Incluye:<br>stock</p></div>", "Necesitamos diseñar el modelo & la capa.\n\nIncluye:\nstock"),
    ("<div>Necesitamos &lt;b&gt;diseñar&lt;/b&gt; algo</div>", "Necesitamos diseñar algo"),                     # etiquetas que llegaron escapadas
    ("<ul><li>Vencimientos</li><li>Reservas</li></ul>", "• Vencimientos\n• Reservas"),
    ("Precio &lt; 5 &amp; ñandú «ok»\r\nsegunda", "Precio < 5 & ñandú «ok»\nsegunda"),
    ("<script>alert(1)</script>texto", "alert(1)texto"),
])
def test_rich_description_becomes_readable_plain_text(raw, expected):
    assert notifications.readable_text(raw) == expected


def test_rich_description_is_rendered_escaped_with_line_breaks():
    html = _render(data={"id": "7", "url": "/x", "description": notifications.readable_text("<p>Uno &lt; dos</p><p><b>Tres</b> & <i>cuatro</i></p>")})
    assert "Uno &lt; dos\n\nTres &amp; cuatro" in html and "<b>" not in html and "white-space:pre-line" in html
