"""
test_notifications_search.py — tercer evento de workflow (`operations.search.created`): Operaciones registra una nueva BÚSQUEDA de
recurso → actúa Capital Humano (To `NOTIFICATIONS_HC_RECIPIENTS`, sin copia). Graph y persistencia SIMULADOS (fixtures de
test_notifications y test_busqueda_solicitud_i1). No hay envíos reales.

Se ejercita el camino de producción real: `svc.create_solicitud` (alta durable) → `operations_busquedas._announce_search_created`
→ `notifications.emit` → plantilla `search_created.html`. La distinción de que SOLO la creación emite (no editar/detalle/navegar)
se fija estructuralmente (el helper se invoca únicamente en la ruta de alta).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from webapp.routers import operations_busquedas as busq
from webapp.services import notifications
from tests.webapp.test_notifications import graph  # noqa: F401 — fixture (habilita notificaciones + Graph simulado)
from tests.webapp.test_busqueda_solicitud_i1 import OPS_A, SID, env, req, send  # noqa: F401 — fixtures/ayudantes de búsqueda

EVENT = "operations.search.created"
ORIGIN = "http://testserver"
ROUTER_SRC = Path(busq.__file__).read_text(encoding="utf-8")


@pytest.fixture
def hc(graph, monkeypatch):  # noqa: F811 — base: notificaciones encendidas + Graph simulado
    """Como `graph`, pero con el destinatario de Capital Humano configurado (dirección de prueba example.com)."""
    monkeypatch.setattr(config, "NOTIFICATIONS_HC_RECIPIENTS", "joconnor@example.com", raising=False)
    return graph


def _records(event=EVENT):
    folder = notifications.record_path(event, "x").parent
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.exists() else []


def _addresses(message, field):
    return [r["emailAddress"]["address"] for r in message.get(field, [])]


def _announce(view, actor=OPS_A):
    busq._announce_search_created(view, actor, ORIGIN)


# ── creación durable → To Capital Humano, SIN copia, contenido y enlace correctos ────────────────────────────────────────────────
def test_created_search_notifies_human_capital_to_only(hc, env):  # noqa: F811
    view = send()
    _announce(view)
    assert len(hc.calls) == 1
    message = hc.calls[0]["body"]["message"]
    assert _addresses(message, "toRecipients") == ["joconnor@example.com"]
    assert "ccRecipients" not in message                                               # evento solo-To: sin copia
    assert message["subject"] == f"Nueva búsqueda de recurso | Operaciones | Búsqueda #{SID}"
    html = message["body"]["content"]
    for text in ("Nueva búsqueda de recurso", f"#{SID}", "Consultor SAP FI", "SAP", "Híbrido", OPS_A):
        assert text in html, text
    assert f'href="{ORIGIN}/operaciones/busquedas/{SID}"' in html                      # enlace directo al detalle
    (record,) = _records()
    assert record["state"] == "accepted" and record["key"] == SID
    assert "s3cret" not in json.dumps(record) and "app-token" not in json.dumps(record)  # nunca token ni secreto


def test_multiple_hc_recipients(hc, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_HC_RECIPIENTS", "uno@example.com; dos@example.com", raising=False)
    _announce(send())
    assert _addresses(hc.calls[0]["body"]["message"], "toRecipients") == ["uno@example.com", "dos@example.com"]


def test_disabled_sends_nothing(hc, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_ENABLED", False, raising=False)
    _announce(send())
    assert not hc.calls and not _records()


def test_incomplete_config_fails_without_graph_call(hc, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "NOTIFICATIONS_SENDER", "", raising=False)
    _announce(send())
    assert not hc.calls                                                                 # falta config obligatoria: no se intenta enviar
    (record,) = _records()
    assert record["state"] == "failed"


def test_graph_error_does_not_revert_the_search(hc, env):  # noqa: F811
    from webapp.services import busqueda_repository
    hc.responses = [400]
    view = send()
    _announce(view)                                                                     # emit nunca lanza: el alta no se revierte
    (record,) = _records()
    assert record["state"] != "accepted"                                               # un error de Graph no marca entregado
    assert busqueda_repository.get(SID) is not None                                     # la búsqueda sigue existiendo (negocio intacto)


def test_resend_is_deduplicated_one_email(hc, env):  # noqa: F811
    view = send()
    _announce(view)
    _announce(view)                                                                     # reintento del alta → misma clave busqueda_id → duplicate
    assert len(hc.calls) == 1 and len(_records()) == 1


def test_reason_free_text_is_rendered_as_safe_html(hc, env):  # noqa: F811
    view = send(nombre_puesto="Dev <script>alert(1)</script> & <b>x</b>")
    _announce(view)
    html = hc.calls[0]["body"]["message"]["body"]["content"]
    assert "<script>" not in html and "<b>x" not in html and "&amp;" in html


# ── SOLO la creación emite: editar/detalle/navegar/CCHH no (estructural) ─────────────────────────────────────────────────────────
def test_only_the_creation_route_announces_the_event():
    # el helper se invoca una sola vez (la llamada exacta) y ESE call site está dentro del cuerpo de la ruta de ALTA (new_submit),
    # es decir antes del decorador de la ruta siguiente. Ninguna otra ruta lo llama.
    call = "_announce_search_created(view, actor, origin)"
    assert ROUTER_SRC.count(call) == 1
    submit_idx = ROUTER_SRC.index("async def operations_busqueda_new_submit")
    next_route_idx = ROUTER_SRC.index("@router.", submit_idx + 1)
    call_idx = ROUTER_SRC.index(call)
    assert submit_idx < call_idx < next_route_idx


# ── regresión: los dos eventos de Arquitectura conservan su política To/Cc ───────────────────────────────────────────────────────
def test_architecture_events_unchanged():
    created = notifications.EVENTS["operations.architecture_request.created"]
    rejected = notifications.EVENTS["operations.architecture_request.rejected"]
    assert created.to_setting == "NOTIFICATIONS_ARQ_RECIPIENTS" and created.cc_setting == "NOTIFICATIONS_OPERATIONS_RECIPIENTS"
    assert rejected.to_setting == "NOTIFICATIONS_OPERATIONS_RECIPIENTS" and rejected.cc_setting == "NOTIFICATIONS_ARQ_RECIPIENTS"
    search = notifications.EVENTS["operations.search.created"]
    assert search.to_setting == "NOTIFICATIONS_HC_RECIPIENTS" and search.cc_setting == ""
