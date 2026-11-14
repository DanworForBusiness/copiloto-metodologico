"""
test_contact_send.py — Capital Humano → 02. Búsquedas → «Contactar candidato» → Mail → «Enviar» (correo institucional a un candidato EXTERNO).

Graph SIMULADO (costura: valores por defecto keyword-only de `institutional_mail.send`). Persistencia aislada con los fixtures de I1. El destinatario se
resuelve siempre en el servidor desde la postulación de la búsqueda; el historial registra UN evento por operación (`event_id` = `operation_id`).
"""
from __future__ import annotations

import json
import urllib.error

import pytest

import config
from webapp.services import busqueda_repository as brepo, human_capital_service as svc, institutional_mail, profile_repository
from webapp.services import human_capital_contact_send as contact_send
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _manual  # noqa: F401

ORIGIN = {"Origin": "http://testserver"}
CAND_EMAIL = "candidata.sintetica@example.com"


class _Graph:
    def __init__(self):
        self.sends, self.tokens, self.outcome = [], [], 202

    def token(self, *a, **k):
        self.tokens.append(a)
        return "app-token-no-registrar"

    def post(self, url, token, body, **_):
        self.sends.append({"url": url, "token": token, "body": body})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture()
def graph(monkeypatch):
    for name, value in {"NOTIFICATIONS_ENABLED": True, "NOTIFICATIONS_TENANT_ID": "t", "NOTIFICATIONS_CLIENT_ID": "c",
                        "NOTIFICATIONS_CLIENT_SECRET": "secreto-no-registrar", "NOTIFICATIONS_SENDER": "hub-notifications@danwor.com"}.items():
        monkeypatch.setattr(config, name, value, raising=False)
    g = _Graph()

    def durable_write(path, text):                                        # Spaces simulado: confirma la escritura durable (el fixture I1 no tiene bucket real)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return institutional_mail.remote_store.DURABLE_REMOTE
    monkeypatch.setattr(institutional_mail.remote_store, "write_through", durable_write)
    monkeypatch.setitem(institutional_mail.send.__kwdefaults__, "token_fn", g.token)
    monkeypatch.setitem(institutional_mail.send.__kwdefaults__, "post", g.post)
    return g


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _candidate(bid, email=CAND_EMAIL, first="Lucía", etapa=None):
    pid = profile_repository.create({"first_name": first, "last_name": "Sintética", "email": email, "phone": "+54 9 11 5555-0000"})["profile_id"]
    aid = svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]
    if etapa:
        svc.move_application_stage(bid, aid, etapa, None, actor=CCHH_EMAIL)
    return pid, aid


def _send(c, bid, aid, op="op-contact-0001", headers=ORIGIN, **extra):
    data = {"operation_id": op, "situacion": "primer_contacto", "asunto": "Oportunidad en Danwor", "mensaje": "Hola, Lucía,\n¿cómo estás?", **extra}
    return c.post(f"/cchh/busquedas/{bid}/candidatos/{aid}/contacto/enviar", data=data, headers=headers)


def _mail_events(bid):
    return [h for h in brepo.historial_proceso_of(brepo.get(bid)) if h["transicion"] == "candidato_contacto" and h["accion"].startswith("correo_")]


@pytest.mark.parametrize("etapa", [None, "Entrevista CCHH", "Entrevista Líder", "Decisión Final"])
def test_send_from_every_pipeline_stage_uses_the_registered_email_and_records_once(c, graph, etapa):
    bid = _manual()
    pid, aid = _candidate(bid, etapa=etapa)
    r = _send(c, bid, aid)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "accepted" and body["recipient"] == CAND_EMAIL and body["history_recorded"] is True
    msg = graph.sends[0]["body"]["message"]
    assert [x["emailAddress"]["address"] for x in msg["toRecipients"]] == [CAND_EMAIL] and msg["subject"] == "Oportunidad en Danwor"
    assert graph.sends[0]["url"].endswith("/users/hub-notifications@danwor.com/sendMail")
    [e] = _mail_events(bid)
    assert (e["accion"], e["canal"], e["event_id"], e["actor"], e["profile_id"]) == ("correo_aceptado", "mail", "op-contact-0001", CCHH_EMAIL, pid)
    assert "Correo aceptado para envío" in body["history_html"]


def test_the_browser_cannot_choose_the_recipient(c, graph):
    bid = _manual()
    _pid, aid = _candidate(bid)
    _send(c, bid, aid, recipient="atacante@example.com", email="atacante@example.com", to="atacante@example.com")
    assert [x["emailAddress"]["address"] for x in graph.sends[0]["body"]["message"]["toRecipients"]] == [CAND_EMAIL]


def test_candidate_must_belong_to_the_search_and_have_a_valid_email(c, graph):
    bid, other = _manual(), _manual()
    _p, aid_other = _candidate(other)
    assert _send(c, bid, aid_other).status_code == 404                                                  # postulación de OTRA búsqueda
    _p, aid_bad = _candidate(bid, email="no-es-correo", first="Sin")
    r = _send(c, bid, aid_bad, op="op-contact-0002")
    assert r.status_code == 422 and r.json()["reason"] == "no_email" and not graph.sends


def test_authorization_same_origin_and_limits(c, graph):
    bid = _manual()
    _p, aid = _candidate(bid)
    assert _send(session_client("otro@danwor.com"), bid, aid).status_code == 403
    assert _send(c, bid, aid, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _send(c, bid, aid, asunto="x" * 201).json()["reason"] == "too_long"
    assert _send(c, bid, aid, mensaje="   ").json()["reason"] == "empty"
    assert _send(c, bid, aid, situacion="inventada").json()["reason"] == "bad_request"
    assert not graph.sends and contact_send.read_audit("op-contact-0001") == {"intent": None, "result": None}


def test_disabled_switch_blocks_backend_and_dialog_keeps_copy(c, graph, monkeypatch):
    bid = _manual()
    _p, aid = _candidate(bid)
    monkeypatch.setattr(config, "NOTIFICATIONS_ENABLED", False)
    r = _send(c, bid, aid)
    assert r.status_code == 503 and r.json()["status"] == "disabled" and not graph.sends and contact_send.read_audit("op-contact-0001")["intent"] is None
    html = c.get(f"/cchh/busquedas/{bid}/candidatos/{aid}/contacto").text
    assert 'data-send-enabled=""' in html and "data-contact-send-disabled" in html and 'data-contact-copy="mensaje"' in html


def test_switch_turned_off_right_before_graph_never_posts(c, graph, monkeypatch):
    bid = _manual()
    _p, aid = _candidate(bid)
    seq = iter([True, True, False])                                       # validación ✓, institutional_mail.send ✓, justo antes del POST ✗
    monkeypatch.setattr(contact_send.notifications, "enabled", lambda: next(seq, False))
    r = _send(c, bid, aid)
    assert r.json()["status"] == "disabled" and not graph.sends and contact_send.read_audit("op-contact-0001")["result"]["outcome"] == "disabled"


def test_double_click_and_retries_never_send_twice(c, graph):
    bid = _manual()
    _p, aid = _candidate(bid)
    first, again = _send(c, bid, aid), _send(c, bid, aid)
    assert first.json()["status"] == "accepted" and again.json()["status"] == "duplicate" and again.json()["prior_outcome"] == "accepted"
    assert len(graph.sends) == 1 and len(_mail_events(bid)) == 1                                       # un envío, un registro


@pytest.mark.parametrize("outcome,status,accion", [
    (urllib.error.HTTPError("u", 400, "Bad", {}, None), "failed", "correo_fallido"),
    (urllib.error.HTTPError("u", 503, "Unavailable", {}, None), "uncertain", "correo_incierto"),
    (TimeoutError(), "uncertain", "correo_incierto"),
])
def test_definitive_error_and_uncertain_result_are_recorded_and_not_resent(c, graph, outcome, status, accion):
    bid = _manual()
    _p, aid = _candidate(bid)
    graph.outcome = outcome
    r = _send(c, bid, aid)
    assert r.json()["status"] == status and [e["accion"] for e in _mail_events(bid)] == [accion]
    assert _send(c, bid, aid).json()["status"] == "duplicate" and len(graph.sends) == 1 and len(_mail_events(bid)) == 1


def test_history_failure_after_202_does_not_resend_and_a_retry_completes_it(c, graph, monkeypatch):
    bid = _manual()
    _p, aid = _candidate(bid)
    real = svc.register_candidate_contact

    def broken(*a, **k):
        raise brepo.BusquedaNotFoundError("caída simulada del historial")
    monkeypatch.setattr(svc, "register_candidate_contact", broken)
    r = _send(c, bid, aid)
    assert r.json()["status"] == "accepted" and r.json()["history_recorded"] is False and not _mail_events(bid)
    monkeypatch.setattr(svc, "register_candidate_contact", real)
    again = _send(c, bid, aid)                                             # mismo operation_id: NO reenvía, completa el historial
    assert again.json()["status"] == "duplicate" and again.json()["history_recorded"] is True
    assert len(graph.sends) == 1 and [e["accion"] for e in _mail_events(bid)] == ["correo_aceptado"]


def test_mail_html_is_safe_keeps_line_breaks_and_has_no_do_not_reply_footer(c, graph):
    bid = _manual()
    _p, aid = _candidate(bid)
    _send(c, bid, aid, mensaje="Hola, Lucía,\n\n<script>alert(1)</script> & <b>negrita</b>\nSaludos")
    html = graph.sends[0]["body"]["message"]["body"]["content"]
    assert "Hola, Lucía,<br><br>&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;negrita&lt;/b&gt;<br>Saludos" in html and "<script>" not in html
    assert "No respondas" not in html and "Equipo de Capital Humano · Danwor" in html
    assert "Capital Humano" in html and "Selección e Incorporación" in html and "attachments" not in graph.sends[0]["body"]["message"]


def test_audit_has_no_body_tokens_or_plain_recipient(c, graph):
    bid = _manual()
    _p, aid = _candidate(bid)
    _send(c, bid, aid, mensaje="Contenido privado del mensaje")
    raw = json.dumps(contact_send.read_audit("op-contact-0001"))
    for forbidden in ("Contenido privado", CAND_EMAIL, "app-token", "secreto"):
        assert forbidden not in raw
    assert "recipient_sha256" in raw and "op-contact-0001" in raw


def test_whatsapp_flow_is_unchanged(c, graph):
    bid = _manual()
    _p, aid = _candidate(bid)
    html = c.get(f"/cchh/busquedas/{bid}/candidatos/{aid}/contacto").text
    assert 'data-contact-open="whatsapp"' in html and "Abrir en WhatsApp" in html and 'data-contact-open="mail"' not in html
    r = c.post(f"/cchh/busquedas/{bid}/candidatos/{aid}/contacto/evento", data={"accion": "canal_abierto", "situacion": "primer_contacto", "canal": "whatsapp", "event_id": "w1"})
    assert r.status_code == 200 and not graph.sends
