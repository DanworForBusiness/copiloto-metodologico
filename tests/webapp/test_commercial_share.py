"""
test_commercial_share.py — Gestión Comercial → «Compartir» (02. Estrategia, presentación individual, presentaciones seleccionadas).

Backend (`commercial_share` + rutas `/comercial/compartir/*`): contexto de destinatarios FIJO `commercial` (rechazo de identidades de otras
verticales), enlaces resueltos por el servidor (kind + ids; nunca URLs del navegador), Graph SIMULADO (nunca hay envíos reales), idempotencia
por `operation_id`, error / incierto / interruptor apagado / auditoría no disponible, plantilla con identidad Gestión Comercial.
Plantillas: selección múltiple del catálogo, diálogo ÚNICO incluido una vez por página, disparadores de Estrategia y del detalle.
Navegador: `commercial_share_dialog.node.js` contra el módulo real (selección, copia, envío).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import config
from webapp.services import auth_service, commercial_share, institutional_mail
from webapp.services import commercial_presentations as cp
from tests.webapp.test_commercial_presentations_access import A, INTRUDER, ORIGIN, SEED_ID, _copy, world  # noqa: F401

_HARNESS = Path(__file__).resolve().parent / "commercial_share_dialog.node.js"
OP = "op-commercial-0001"
REAL_SEND = institutional_mail.send                                   # el transporte real, antes de simularlo en el fixture


@pytest.fixture
def mail(world, tmp_path, monkeypatch):
    """Comercial con proveedor allowlist, correo institucional configurado (explícito: nunca hereda el .env local) y transporte simulado."""
    monkeypatch.setattr(config, "AUTH_DEFAULT_DOMAIN", "danwor.com", raising=False)
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "hc-x")
    monkeypatch.setattr(config, "NOTIFICATIONS_DIR", tmp_path / "notifications")
    for name, value in {"NOTIFICATIONS_TENANT_ID": "tenant-x", "NOTIFICATIONS_CLIENT_ID": "client-x", "NOTIFICATIONS_CLIENT_SECRET": "s3cr3t-never-logged",
                        "NOTIFICATIONS_SENDER": "hub-notifications@danwor.com", "NOTIFICATIONS_ENABLED": True, "NOTIFICATIONS_HUB_BASE_URL": "",
                        "SHARE_RECIPIENTS_PROVIDER": "allowlist", "HC_SHARE_RECIPIENTS_PROVIDER": ""}.items():
        monkeypatch.setattr(config, name, value, raising=False)
    sent: list[dict] = []
    outcome = {"state": ("accepted", "HTTP 202")}

    def fake_send(message):
        sent.append(message)
        return outcome["state"]
    monkeypatch.setattr(institutional_mail, "send", fake_send)
    world.sent, world.outcome = sent, outcome
    return world


def _send(world, email=A, **form):
    data = {"operation_id": OP, "kind": "presentations", "presentation_ids": [SEED_ID], "recipients": ["com-b@danwor.com"],
            "subject": "", "message": "Hola,\n\nTe comparto esto."}
    data.update(form)
    return world.client(email).post("/comercial/compartir/enviar", data=data, headers=ORIGIN)


# ───────────────────────── backend ─────────────────────────

def test_send_resolves_links_on_the_server_and_uses_the_commercial_identity(mail):
    r = _send(mail)
    assert r.status_code == 200 and r.json()["status"] == "accepted", r.text
    assert len(mail.sent) == 1
    msg = mail.sent[0]["message"]
    html = msg["body"]["content"]
    assert msg["toRecipients"] == [{"emailAddress": {"address": "com-b@danwor.com"}}]
    assert f'href="http://testserver/presentaciones/{SEED_ID}"' in html and ">Abrir en el Hub<" in html     # enlace del servidor, texto legible
    assert f">http://testserver/presentaciones/{SEED_ID}<" not in html                                     # la URL larga no se muestra
    assert "Gestión Comercial" in html or "Comercial" in html
    assert msg["subject"].startswith("Presentación: ")
    audit = commercial_share.read_audit(OP)
    assert audit["intent"]["paths"] == [f"/presentaciones/{SEED_ID}"] and audit["result"]["outcome"] == "accepted"
    assert "Te comparto" not in json.dumps(audit) and "s3cr3t" not in json.dumps(audit)                   # sin cuerpo ni secretos en la auditoría


def test_strategy_shares_its_existing_page_and_ignores_any_browser_ids(mail):
    r = _send(mail, kind="strategy", presentation_ids=["https://evil.example/x"])
    assert r.json()["status"] == "accepted"
    html = mail.sent[0]["message"]["body"]["content"]
    assert 'href="http://testserver/comercial/estrategia"' in html and "evil.example" not in html


def test_several_presentations_and_unknown_ids_are_omitted(mail):
    copy_id = _copy(mail, A)
    r = _send(mail, presentation_ids=[SEED_ID, copy_id, "no-existe", SEED_ID])
    body = r.json()
    assert body["status"] == "accepted" and body["omitted"] == ["no-existe"] and len(body["shared"]) == 2
    assert mail.sent[0]["message"]["subject"] == "Presentaciones comerciales (2)"


def test_recipients_outside_the_commercial_allowlist_are_rejected(mail):
    r = _send(mail, recipients=["hc-x@danwor.com", "externo@gmail.com"])
    assert r.status_code == 422 and r.json()["reason"] == "no_recipients" and mail.sent == []
    r = _send(mail, operation_id="op-commercial-0002", recipients=["hc-x@danwor.com", "com-b@danwor.com"])
    assert r.json()["status"] == "accepted" and r.json()["invalid"] == ["hc-x@danwor.com"]
    assert mail.sent[0]["message"]["toRecipients"] == [{"emailAddress": {"address": "com-b@danwor.com"}}]


def test_same_operation_never_sends_twice(mail):
    assert _send(mail).json()["status"] == "accepted"
    again = _send(mail).json()
    assert again["status"] == "duplicate" and again["prior_outcome"] == "accepted" and len(mail.sent) == 1


@pytest.mark.parametrize("state,expected", [(("failed", "Graph HTTP 400"), "failed"), (("uncertain", "Graph HTTP 503"), "uncertain")])
def test_failed_and_uncertain_outcomes_are_reported_and_block_the_same_operation(mail, state, expected):
    mail.outcome["state"] = state
    r = _send(mail)
    assert r.json()["status"] == expected and commercial_share.read_audit(OP)["result"]["outcome"] == expected
    assert _send(mail).json()["status"] == "duplicate" and len(mail.sent) == 1


def test_global_switch_off_rejects_without_audit_or_graph(mail, monkeypatch):
    monkeypatch.setattr(config, "NOTIFICATIONS_ENABLED", False)
    r = _send(mail)
    assert r.status_code == 503 and r.json()["status"] == "disabled" and mail.sent == []
    assert commercial_share.read_audit(OP)["intent"] is None


def test_audit_unavailable_fails_closed(mail, monkeypatch):
    def boom(*a, **k):
        raise institutional_mail.AuditUnavailable("x")
    monkeypatch.setattr(institutional_mail, "write_audit", boom)
    r = _send(mail)
    assert r.status_code == 503 and r.json()["reason"] == "audit_unavailable" and mail.sent == []


def test_real_transport_posts_once_to_graph_with_the_app_identity(mail):
    """Transporte REAL de `institutional_mail` con token y POST simulados (nada sale del proceso): un POST a `sendMail` del buzón institucional."""
    calls = []
    out = commercial_share.send_share(
        operation_id=OP, actor=A, kind="strategy", presentation_ids=[], recipient_emails=["com-b@danwor.com"], subject="", message="",
        delegated_token="", origin="http://testserver", get_presentation=cp.get,
        send=lambda m: REAL_SEND(m, token_fn=lambda *a: "app-token", post=lambda url, token, payload: calls.append((url, token, payload)) or 202))
    assert out["status"] == "accepted" and len(calls) == 1
    url, token, payload = calls[0]
    assert url.endswith("/users/hub-notifications@danwor.com/sendMail") and token == "app-token"
    assert payload["message"]["subject"] == "Estrategia comercial"


def test_routes_require_session_commercial_and_same_origin(mail):
    assert mail.client(None).post("/comercial/compartir/enviar", data={}, headers=ORIGIN).status_code == 401
    assert mail.client(INTRUDER).post("/comercial/compartir/enviar", data={}, headers=ORIGIN).status_code == 403
    assert mail.client(A).post("/comercial/compartir/enviar", data={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert mail.sent == []


def test_catalog_lists_only_commercial_recipients_and_is_closed_for_the_directory_provider(mail, monkeypatch):
    monkeypatch.setattr("webapp.services.share_recipients.roster_names", lambda token: {})
    r = mail.client(A).post("/comercial/compartir/catalogo", data={"graph_token": ""}, headers=ORIGIN)
    emails = [x["email"] for x in r.json()["recipients"]]
    assert r.status_code == 200 and "hc-x@danwor.com" not in emails and "com-b@danwor.com" in emails
    s = mail.client(A).post("/comercial/compartir/destinatarios", data={"q": "com"}, headers=ORIGIN).json()
    assert all(x["email"].startswith("com-") or x["email"].startswith("owner-") for x in s["results"])
    monkeypatch.setattr(config, "SHARE_RECIPIENTS_PROVIDER", "directory")
    assert mail.client(A).post("/comercial/compartir/catalogo", data={}, headers=ORIGIN).status_code == 404


# ───────────────────────── plantillas ─────────────────────────

def _soup(world, url):
    r = world.client(A).get(url)
    assert r.status_code == 200, r.text
    return BeautifulSoup(r.text, "html.parser")


def test_catalog_has_row_selection_bar_and_a_single_shared_dialog(mail):
    soup = _soup(mail, "/comercial/presentaciones")
    table = soup.select_one("#cp-catalog-table")
    assert table.select_one("thead [data-cp-select-page]") is not None
    boxes = table.select("tbody [data-cp-select]")
    assert boxes and all(b.get("data-cshare-path", "").startswith("/presentaciones/") for b in boxes)
    bar = soup.select_one(".hc-selection-bar[data-cp-selection-bar]")
    assert bar is not None and bar.has_attr("hidden")
    share = bar.select_one("button.btn-primary[data-cshare-open=selection]")
    assert share is not None and share.has_attr("disabled") and "Compartir presentaciones" in share.text
    assert bar.select_one("[data-cp-clear-selection]") is not None
    assert len(soup.select("#cshare-dialog")) == 1
    assert len(soup.select("[data-cp-copy-link]")) >= 1                                           # acciones individuales intactas
    assert "table_selection.js" in str(soup) and "share_recipients.js" in str(soup) and "commercial_share.js" in str(soup)


def test_detail_and_strategy_open_the_same_dialog_with_their_existing_links(mail):
    detail = _soup(mail, f"/comercial/presentaciones/{SEED_ID}")
    btn = detail.select_one(".cp-toolbar [data-cshare-open]")
    assert btn is not None and btn.text.strip() == "Compartir" and detail.select_one(".cp-toolbar [data-cp-copy-link]") is None
    assert json.loads(btn["data-cshare-items"])[0]["path"] == f"/presentaciones/{SEED_ID}"
    assert len(detail.select("#cshare-dialog")) == 1
    strategy = _soup(mail, "/comercial/estrategia")
    sbtn = strategy.select_one("#cs-share-btn")
    assert sbtn.get("data-cshare-kind") == "strategy" and json.loads(sbtn["data-cshare-items"])[0]["path"] == "/comercial/estrategia"
    assert len(strategy.select("#cshare-dialog")) == 1
    dlg = strategy.select_one("#cshare-dialog")
    assert dlg["data-catalog-url"] == "/comercial/compartir/catalogo" and dlg["data-send-url"] == "/comercial/compartir/enviar"   # rutas de la vertical


# ───────────────────────── navegador ─────────────────────────

@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_browser_module_selection_copy_and_send():
    out = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip().endswith("OK"), out.stdout + out.stderr
