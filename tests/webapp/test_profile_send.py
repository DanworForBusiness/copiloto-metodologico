"""
test_profile_send.py — Capital Humano → 01. Candidatos → Compartir perfiles → «Enviar por correo» (Caso B, `human_capital_profile_send`).

Directorio Microsoft Entra y Graph `sendMail` SIMULADOS: nunca hay envíos reales. Las costuras son los parámetros keyword-only que el servicio ya
expone (`fetch` de `search_directory`/`validate_recipients`, `token_fn`/`post` de `send_graph_message`); se reemplazan sus valores por defecto
durante cada test, así las rutas reales ejercitan TODO el flujo (sesión, autorización, mismo origen, resolución de candidatos, validación de
destinatarios, auditoría durable y armado del correo).

El comportamiento del navegador (chips, deduplicación, confirmación explícita, doble clic, Copiar/Abrir Outlook) se prueba con el harness Node
`profile_send_dialog.node.js` contra el módulo real `human_capital_candidates_selection.js`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access
from webapp.main import app
from webapp.services import auth_service, profile_document_repository, profile_repository
from webapp.services import human_capital_profile_send as profile_send

HC = "hc-send-test@danwor.com"
ORIGIN = {"Origin": "http://testserver"}
DELEGATED = "delegated-user-token-no-debe-registrarse"
APP_TOKEN = "app-token-no-debe-registrarse"
SECRET = "client-secret-no-debe-registrarse"
SP = "https://danwor.sharepoint.com/sites/cchh/"
_HARNESS = Path(__file__).resolve().parent / "profile_send_dialog.node.js"

DIRECTORY = {
    "ana.directorio@danwor.com": {"id": "u1", "displayName": "Ana Directorio", "mail": "ana.directorio@danwor.com", "userPrincipalName": "ana.directorio@danwor.com"},
    "bruno.directorio@danwor.com": {"id": "u2", "displayName": "Bruno Directorio", "mail": "bruno.directorio@danwor.com", "userPrincipalName": "bruno.directorio@danwor.com"},
}


class _Graph:
    """Directorio Entra + sendMail simulados. `search`/`gets` registran el token DELEGADO; `sends` el token de APP y el mensaje."""

    def __init__(self):
        self.search_calls, self.get_calls, self.sends, self.tokens = [], [], [], []
        self.search_error = None          # HTTPError / excepción para /users?$search
        self.get_error = None             # HTTPError para /users/{id} (p. ej. 403 sin consentimiento)
        self.send_outcome = 202           # int (status) o excepción

    def fetch(self, url, token, extra_headers=None, **_):
        if "/users?$search=" in url:
            self.search_calls.append({"url": url, "token": token, "headers": dict(extra_headers or {})})
            if self.search_error:
                raise self.search_error
            q = url.split("displayName:", 1)[1].split("%22", 1)[0].lower()
            rows = [u for u in DIRECTORY.values() if q in u["displayName"].lower() or q in u["mail"].lower()]
            return {"value": rows + rows[:1]}                                   # Graph puede repetir: el servicio deduplica
        self.get_calls.append({"url": url, "token": token})
        if self.get_error:
            raise self.get_error
        key = url.split("/users/", 1)[1].split("?", 1)[0].lower()
        if key not in DIRECTORY:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return DIRECTORY[key]

    def token(self, tenant, client_id, secret, **_):
        self.tokens.append((tenant, client_id, secret == SECRET))
        return APP_TOKEN

    def post(self, url, token, body, **_):
        self.sends.append({"url": url, "token": token, "body": body})
        if isinstance(self.send_outcome, BaseException):
            raise self.send_outcome
        return self.send_outcome


@pytest.fixture
def graph(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", HC)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(access, "APP_OWNER", "owner-send@danwor.com")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    for name, value in {"NOTIFICATIONS_TENANT_ID": "tenant-x", "NOTIFICATIONS_CLIENT_ID": "client-x", "NOTIFICATIONS_CLIENT_SECRET": SECRET,
                        "NOTIFICATIONS_SENDER": "hub-notifications@danwor.com", "HC_DIRECTORY_SEARCH_SCOPE": "User.ReadBasic.All",
                        "HC_SHARE_RECIPIENTS_PROVIDER": "directory"}.items():                                  # explícito: nunca hereda el .env local
        monkeypatch.setattr(config, name, value, raising=False)
    g = _Graph()
    monkeypatch.setitem(profile_send.search_directory.__kwdefaults__, "fetch", g.fetch)
    monkeypatch.setitem(profile_send.validate_recipients.__kwdefaults__, "fetch", g.fetch)
    monkeypatch.setitem(profile_send.send_graph_message.__kwdefaults__, "token_fn", g.token)
    monkeypatch.setitem(profile_send.send_graph_message.__kwdefaults__, "post", g.post)
    return g


def client(email: str | None = HC) -> TestClient:
    c = TestClient(app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def make_profile(first="Ana", last="Candidata", **extra) -> dict:
    return profile_repository.create({"first_name": first, "last_name": last, "email": f"{first}.{last}@sintetico.test".lower(), "phone": "+54 11 5555-0000",
                                      "notes": "nota privada del candidato", **extra})


def add_doc(profile_id, document_id="doc-1", web_url=SP + "cv.pdf", uploaded_at="2026-10-01T00:00:00+00:00") -> dict:
    doc = {"document_id": document_id, "profile_id": profile_id, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": f"i-{document_id}",
           "filename": f"{document_id}.pdf", "content_type": "application/pdf", "size_bytes": 2048, "uploaded_at": uploaded_at, "uploaded_by": "rrhh@test",
           "web_url": web_url}
    return profile_document_repository.create_with_explicit_id(document_id, doc)[0]


def candidate(first="Ana", last="Candidata", **doc) -> str:
    p = make_profile(first, last)
    add_doc(p["profile_id"], document_id=doc.pop("document_id", f"doc-{first.lower()}"), **doc)
    return p["profile_id"]


def send(c, profile_ids, recipients, operation_id="op-test-000001", **extra):
    data = {"profile_ids": profile_ids, "recipients": recipients, "operation_id": operation_id, "graph_token": DELEGATED,
            "subject": "Perfiles", "message": "Hola", **extra}
    return c.post("/cchh/candidatos/compartir/enviar", data=data, headers=ORIGIN)


def search(c, q, **extra):
    return c.post("/cchh/candidatos/compartir/destinatarios", data={"q": q, "graph_token": extra.pop("graph_token", DELEGATED)}, headers=extra.pop("headers", ORIGIN))


def audit(operation_id="op-test-000001"):
    return profile_send.read_audit(operation_id)


def html_of(g, i=0):
    return g.sends[i]["body"]["message"]["body"]["content"]


def to_of(g, i=0):
    return [r["emailAddress"]["address"] for r in g.sends[i]["body"]["message"]["toRecipients"]]


# ── 1–2 · candidatos y enlaces ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_one_or_several_candidates_are_sent_with_their_system_links(graph):
    a = candidate("Ana", "Uno", web_url=SP + "ana.pdf")
    b = candidate("Bruno", "Dos", web_url=SP + "bruno.pdf")
    r = send(client(), [a], ["ana.directorio@danwor.com"])
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["shared"] == ["Ana Uno"]
    r = send(client(), [a, b], ["ana.directorio@danwor.com"], operation_id="op-test-000002")
    assert r.json()["shared"] == ["Ana Uno", "Bruno Dos"]
    html = html_of(graph, 1)
    assert f'href="{SP}ana.pdf"' in html and f'href="{SP}bruno.pdf"' in html


def test_links_come_only_from_the_system_and_unsafe_ones_are_omitted(graph):
    good = candidate("Ana", "Segura", web_url=SP + "ok.pdf")
    bad = candidate("Bruno", "Insegura", web_url="http://evil.example/cv.pdf")
    r = send(client(), [good, bad], ["ana.directorio@danwor.com"], url="https://evil.example/forjado.pdf")   # un campo ajeno del navegador no se usa
    body = r.json()
    assert body["status"] == "accepted" and body["shared"] == ["Ana Segura"] and body["omitted"] == ["Bruno Insegura"]
    html = html_of(graph)
    assert "evil.example" not in html and f'href="{SP}ok.pdf"' in html


def test_with_several_cvs_the_chosen_one_is_sent_and_none_without_choice(graph):
    p = make_profile("Carla", "Varios")["profile_id"]
    add_doc(p, "doc-old", web_url=SP + "viejo.pdf", uploaded_at="2026-01-01T00:00:00+00:00")
    add_doc(p, "doc-new", web_url=SP + "nuevo.pdf", uploaded_at="2026-10-01T00:00:00+00:00")
    r = send(client(), [p], ["ana.directorio@danwor.com"])                                     # ambiguo sin elección → sin enlace → no se envía
    assert r.status_code == 422 and r.json()["reason"] == "no_links" and not graph.sends
    r = send(client(), [p], ["ana.directorio@danwor.com"], choice=json.dumps({p: "doc-old"}))
    assert r.json()["status"] == "accepted" and f'href="{SP}viejo.pdf"' in html_of(graph) and "nuevo.pdf" not in html_of(graph)


# ── 3 + 6 · búsqueda en el directorio (token delegado) ──────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q,expected", [("Bruno", ["bruno.directorio@danwor.com"]), ("ana.dir", ["ana.directorio@danwor.com"])])
def test_directory_search_by_name_or_mail_uses_the_delegated_token_and_dedupes(graph, q, expected):
    r = search(client(), q)
    assert r.status_code == 200 and [x["email"] for x in r.json()["results"]] == expected            # Graph devolvió repetidos: quedan únicos
    call = graph.search_calls[0]
    assert call["token"] == DELEGATED and call["headers"] == {"ConsistencyLevel": "eventual"}
    assert "displayName:" in call["url"] and "mail:" in call["url"] and "$select=id,displayName,mail,userPrincipalName" in call["url"]


def test_directory_search_needs_three_characters_and_reports_no_results(graph):
    assert search(client(), "an").json() == {"results": []} and not graph.search_calls
    assert search(client(), "zzzz").json() == {"results": []}


def test_directory_search_is_blocked_without_consent_or_scope(graph, monkeypatch):
    graph.search_error = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    r = search(client(), "Ana")
    assert r.status_code == 503 and r.json()["blocked"] is True
    monkeypatch.setattr(config, "HC_DIRECTORY_SEARCH_SCOPE", "")
    graph.search_calls.clear()
    r = search(client(), "Ana")
    assert r.status_code == 503 and r.json()["blocked"] is True and not graph.search_calls           # sin scope ni se consulta Graph


def test_directory_transient_error_is_retryable_not_blocking(graph):
    graph.search_error = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
    r = search(client(), "Ana")
    assert r.status_code == 502 and "blocked" not in r.json()


def test_directory_search_requires_session_cchh_same_origin_and_token(graph):
    assert search(TestClient(app), "Ana").status_code == 401
    assert search(client("otro@danwor.com"), "Ana").status_code == 403
    assert search(client(), "Ana", headers={"Origin": "https://evil.example"}).status_code == 403
    assert search(client(), "Ana", graph_token="").status_code == 401
    assert not graph.search_calls


# ── 4 + 6 + 7 + 8 · destinatarios validados en el backend ───────────────────────────────────────────────────────────────────────────────────
def test_several_recipients_are_validated_deduped_and_sent_in_to(graph):
    a = candidate()
    r = send(client(), [a], ["ana.directorio@danwor.com", "BRUNO.directorio@danwor.com", "Ana.Directorio@danwor.com"])
    assert r.json()["status"] == "accepted"
    assert to_of(graph) == ["ana.directorio@danwor.com", "bruno.directorio@danwor.com"]              # resueltos por el directorio, sin duplicados
    assert all(c["token"] == DELEGATED for c in graph.get_calls) and len(graph.get_calls) == 2


def test_recipients_outside_the_directory_are_rejected_and_never_receive(graph):
    a = candidate()
    r = send(client(), [a], ["ana.directorio@danwor.com", "externo@gmail.com", "no-es-correo"])
    body = r.json()
    assert body["status"] == "accepted" and to_of(graph) == ["ana.directorio@danwor.com"]
    assert set(body["invalid"]) == {"externo@gmail.com", "no-es-correo"}


@pytest.mark.parametrize("recipients", [[], ["externo@gmail.com"]])
def test_without_valid_recipients_nothing_is_sent_or_audited(graph, recipients):
    a = candidate()
    r = send(client(), [a], recipients)
    assert r.status_code == 422 and r.json()["reason"] == "no_recipients"
    assert not graph.sends and audit() == {"intent": None, "result": None}


def test_validation_without_directory_consent_blocks_the_send(graph):
    graph.get_error = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    r = send(client(), [candidate()], ["ana.directorio@danwor.com"])
    assert r.status_code == 503 and not graph.sends and audit()["intent"] is None


def test_too_many_recipients_are_refused(graph, monkeypatch):
    monkeypatch.setattr(profile_send, "MAX_RECIPIENTS", 1)
    r = send(client(), [candidate()], ["ana.directorio@danwor.com", "bruno.directorio@danwor.com"])
    assert r.status_code == 422 and r.json()["reason"] == "too_many_recipients" and not graph.sends


# ── 9 · candidatos inexistentes / usuarios no autorizados ───────────────────────────────────────────────────────────────────────────────────
def test_missing_or_deleted_candidates_are_never_sent(graph):
    gone = candidate("Dada", "DeBaja")
    profile_repository.soft_delete(gone, "rrhh@test")
    for ids in (["no-existe-123"], [gone]):
        r = send(client(), ids, ["ana.directorio@danwor.com"])
        assert r.status_code == 422 and r.json()["reason"] == "none_available" and not graph.sends
    r = send(client(), ["no-existe-123", candidate()], ["ana.directorio@danwor.com"])                   # mezcla: solo el disponible
    assert r.json()["status"] == "accepted" and r.json()["shared"] == ["Ana Candidata"]
    r = send(client(), [], ["ana.directorio@danwor.com"])
    assert r.status_code == 422 and r.json()["reason"] == "empty"


def test_send_requires_session_cchh_authorization_same_origin_and_delegated_token(graph):
    a = candidate()
    assert send(TestClient(app), [a], ["ana.directorio@danwor.com"]).status_code == 401
    assert send(client("otro@danwor.com"), [a], ["ana.directorio@danwor.com"]).status_code == 403
    assert client().post("/cchh/candidatos/compartir/enviar", data={"profile_ids": [a], "recipients": ["ana.directorio@danwor.com"],
                                                                     "operation_id": "op-test-000001", "graph_token": DELEGATED},
                         headers={"Origin": "https://evil.example"}).status_code == 403
    assert send(client(), [a], ["ana.directorio@danwor.com"], graph_token="").status_code == 401
    assert not graph.sends and not graph.get_calls


# ── 11 + 12 · anti doble envío y segundo envío deliberado ───────────────────────────────────────────────────────────────────────────────────
def test_same_operation_is_sent_once_and_a_new_operation_sends_again(graph):
    a = candidate()
    first = send(client(), [a], ["ana.directorio@danwor.com"])
    again = send(client(), [a], ["ana.directorio@danwor.com"])                                       # doble clic / reintento del mismo diálogo
    assert first.json()["status"] == "accepted" and again.json() == {"status": "duplicate", "message": "Este envío ya se había registrado.", "prior_outcome": "accepted"}
    assert len(graph.sends) == 1
    third = send(client(), [a], ["ana.directorio@danwor.com"], operation_id="op-test-000002")         # nueva apertura del diálogo = envío deliberado
    assert third.json()["status"] == "accepted" and len(graph.sends) == 2


@pytest.mark.parametrize("op", ["", "corto", "con espacios 123", "x" * 65])
def test_invalid_operation_ids_are_refused(graph, op):
    r = send(client(), [candidate()], ["ana.directorio@danwor.com"], operation_id=op)
    assert r.status_code == 422 and r.json()["reason"] == "bad_operation" and not graph.sends


# ── 13–16 · resultados de Graph y auditoría durable ─────────────────────────────────────────────────────────────────────────────────────────
def test_accepted_uses_the_institutional_app_identity_and_audits_intent_and_result(graph):
    r = send(client(), [candidate()], ["ana.directorio@danwor.com"])
    assert r.json()["status"] == "accepted"
    call = graph.sends[0]
    assert call["url"] == "https://graph.microsoft.com/v1.0/users/hub-notifications@danwor.com/sendMail"
    assert call["token"] == APP_TOKEN and graph.tokens == [("tenant-x", "client-x", True)]           # envía la APP; jamás el token delegado
    rec = audit()
    assert rec["intent"]["actor"] == HC and rec["intent"]["recipients"] == ["ana.directorio@danwor.com"] and rec["intent"]["shared_count"] == 1
    assert rec["result"]["outcome"] == "accepted" and rec["result"]["detail"] == "HTTP 202"


@pytest.mark.parametrize("outcome,status,code", [
    (urllib.error.HTTPError("u", 400, "Bad", {}, None), "failed", 422),
    (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "failed", 422),
    (urllib.error.HTTPError("u", 429, "Throttled", {}, None), "uncertain", 200),
    (urllib.error.HTTPError("u", 500, "Error", {}, None), "uncertain", 200),
    (TimeoutError(), "uncertain", 200),
    (200, "uncertain", 200),
])
def test_graph_errors_and_uncertain_results_are_audited_and_never_retried(graph, outcome, status, code):
    graph.send_outcome = outcome
    r = send(client(), [candidate()], ["ana.directorio@danwor.com"])
    assert r.status_code == code and r.json()["status"] == status
    assert audit()["result"]["outcome"] == status and len(graph.sends) == 1
    again = send(client(), [candidate("Otra", "Persona")], ["ana.directorio@danwor.com"])              # mismo operation_id: nunca se reenvía a ciegas
    assert again.json()["status"] == "duplicate" and len(graph.sends) == 1


def test_incomplete_mail_configuration_sends_nothing_and_leaves_no_orphan_intent(graph, monkeypatch):
    monkeypatch.setattr(config, "NOTIFICATIONS_SENDER", "")
    r = send(client(), [candidate()], ["ana.directorio@danwor.com"])
    assert r.status_code == 503 and r.json()["status"] == "config_incomplete" and not graph.sends and audit()["intent"] is None


def test_audit_failure_blocks_the_send(graph, monkeypatch):
    def unavailable(*a, **k):
        raise profile_send.SendAuditUnavailable("x")
    monkeypatch.setattr(profile_send, "write_intent", unavailable)
    r = send(client(), [candidate()], ["ana.directorio@danwor.com"])
    assert r.status_code == 503 and r.json()["reason"] == "audit_unavailable" and not graph.sends


# ── 17 · sin secretos, tokens ni contenido sensible en la auditoría ─────────────────────────────────────────────────────────────────────────
def test_audit_has_no_tokens_secrets_links_or_candidate_personal_data(graph):
    a = candidate("Ana", "Privada", web_url=SP + "privado.pdf")
    send(client(), [a], ["ana.directorio@danwor.com"], message="mensaje del usuario")
    raw = json.dumps(audit(), ensure_ascii=False)
    for forbidden in (DELEGATED, APP_TOKEN, SECRET, "privado.pdf", "sharepoint.com", "Ana Privada", "sintetico.test", "+54 11", "nota privada", "mensaje del usuario"):
        assert forbidden not in raw, forbidden
    assert a in raw                                                                                    # solo el identificador opaco


def test_mail_body_carries_only_names_and_links_and_escapes_the_user_message(graph):
    a = candidate("Ana", "Cuerpo")
    send(client(), [a], ["ana.directorio@danwor.com"], message="<script>alert(1)</script> hola", subject="Asunto <b>x</b>")
    msg = graph.sends[0]["body"]["message"]
    html = msg["body"]["content"]
    assert "<script>" not in html and "sintetico.test" not in html and "+54 11" not in html and "nota privada" not in html
    assert "permisos de SharePoint del destinatario" in html and "Ana Cuerpo" in html
    assert "attachments" not in msg and msg["subject"] == "Asunto <b>x</b>"                          # asunto en texto plano (Graph no lo interpreta como HTML)


# ── 18 · el diálogo conserva Copiar y Abrir Outlook (con y sin directorio) ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("scope", ["User.ReadBasic.All", ""])
def test_dialog_keeps_copy_and_outlook_and_reflects_directory_availability(graph, monkeypatch, scope):
    monkeypatch.setattr(config, "HC_DIRECTORY_SEARCH_SCOPE", scope)
    a = candidate()
    html = client().post("/cchh/candidatos/compartir", data={"profile_ids": [a]}, headers=ORIGIN).text
    assert 'data-share-copy="mensaje"' in html and "data-share-open" in html and "data-share-send disabled" in html
    assert ("data-share-dir-blocked hidden" in html) == bool(scope)                                   # aviso de permiso pendiente solo sin scope
    assert (f'data-directory-enabled="{"1" if scope else ""}"') in html


# ── navegador: chips, deduplicación, confirmación, doble clic (harness Node sobre el módulo real) ───────────────────────────────────────────
def test_dialog_recipient_selector_confirmation_and_double_click_in_the_browser_module():
    node = shutil.which("node")
    if not node:
        pytest.skip("node no disponible")
    out = subprocess.run([node, str(_HARNESS)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ALL PROFILE SEND DIALOG CHECKS PASSED" in out.stdout


# ── proveedor TRANSITORIO «allowlist» (catálogo gobernado = HUMAN_CAPITAL_ALLOWED_USERS), explícito y retirable ──────────────────────────
@pytest.fixture
def allowlist(graph, monkeypatch):
    monkeypatch.setattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", "allowlist", raising=False)
    monkeypatch.setattr(config, "HC_DIRECTORY_SEARCH_SCOPE", "")                                     # sin permiso de directorio: el caso real de hoy
    monkeypatch.setattr(config, "AUTH_DEFAULT_DOMAIN", "danwor.com", raising=False)
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{HC}, Compacto.Usuario , externo@gmail.com")
    return graph


def test_allowlist_catalog_derives_corporate_addresses_only():
    from webapp.services import auth_service as a  # noqa: F401 — mismo parser que la admisión
    import config as c
    old = (a.HUMAN_CAPITAL_ALLOWED_USERS, getattr(c, "AUTH_DEFAULT_DOMAIN", "danwor.com"))
    try:
        a.HUMAN_CAPITAL_ALLOWED_USERS = "Ana.Uno, bruno@danwor.com, externo@gmail.com, ALL"
        c.AUTH_DEFAULT_DOMAIN = "danwor.com"
        emails = [r["email"] for r in profile_send.allowlist_recipients()]
        assert emails == ["ana.uno@danwor.com", "bruno@danwor.com"]                                  # compacto normalizado; sin externos ni el especial «ALL»
        assert all(r["name"] == r["email"] for r in profile_send.allowlist_recipients())             # sin nombres gobernados: el correo es el nombre visible
    finally:
        a.HUMAN_CAPITAL_ALLOWED_USERS, c.AUTH_DEFAULT_DOMAIN = old


def test_provider_is_explicit_and_never_a_fallback_for_graph_errors(graph, monkeypatch):
    for value in ("", "directory", "ALLOW", "otro"):
        monkeypatch.setattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", value, raising=False)
        assert profile_send.recipient_provider() == "directory"
    monkeypatch.setattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", "directory", raising=False)
    graph.search_error = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    assert search(client(), "hc-").status_code == 503                                               # el directorio bloqueado NO cae al catálogo
    graph.search_error = urllib.error.HTTPError("u", 503, "Unavailable", {}, None)
    assert search(client(), "hc-").status_code == 502


def test_allowlist_search_uses_the_catalog_without_microsoft_or_token(allowlist):
    r = search(client(), "hc-send", graph_token="")
    assert r.status_code == 200 and r.json()["results"] == [{"name": HC, "email": HC}]
    assert search(client(), "compacto", graph_token="").json()["results"] == [{"name": "compacto.usuario@danwor.com", "email": "compacto.usuario@danwor.com"}]
    assert search(client(), "gmail", graph_token="").json()["results"] == []                        # el externo no existe para el selector
    assert search(client(), "hc", graph_token="").json()["results"] == []                           # mismo mínimo de 3 caracteres
    assert not allowlist.search_calls and not allowlist.get_calls


def test_allowlist_send_validates_against_the_catalog_and_uses_the_institutional_mailbox(allowlist):
    a = candidate()
    r = send(client(), [a], [HC.upper(), "externo@gmail.com", "otro@danwor.com"], graph_token="")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "accepted" and to_of(allowlist) == [HC]
    assert set(body["invalid"]) == {"externo@gmail.com", "otro@danwor.com"}                         # ni externos ni corporativos fuera del catálogo
    assert allowlist.sends[0]["token"] == APP_TOKEN and not allowlist.get_calls                     # sin consultas a Microsoft con identidad delegada
    assert audit()["intent"]["recipients"] == [HC] and audit()["result"]["outcome"] == "accepted"


def test_allowlist_without_catalog_entries_stays_blocked(allowlist, monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "externo@gmail.com")
    owner = client("owner-send@danwor.com")                                                          # el owner entra a CCHH sin estar en la lista
    assert search(owner, "externo", graph_token="").status_code == 503
    html = owner.post("/cchh/candidatos/compartir", data={"profile_ids": [candidate()]}, headers=ORIGIN).text
    assert 'data-directory-enabled=""' in html and "data-share-dir-blocked hidden" not in html


def test_allowlist_dialog_announces_the_transitional_mode_and_keeps_the_same_ui(allowlist):
    html = client().post("/cchh/candidatos/compartir", data={"profile_ids": [candidate()]}, headers=ORIGIN).text
    assert 'data-recipient-provider="allowlist"' in html and 'data-directory-enabled="1"' in html and "data-share-provider-note" in html
    for marker in ("data-share-search", "data-share-chips", "data-share-send", 'data-share-copy="mensaje"', "data-share-open"):
        assert marker in html


# ── «allowlist» con nombres del padrón (Colaboradores + User Information List, vía ReportingRepository) ─────────────────────────────────
ROSTER = {HC: "Helena Castro Ruiz", "colaborador.ajeno@danwor.com": "Persona Fuera De La Lista", "otro.ajeno@danwor.com": "Otra Persona"}


@pytest.fixture
def roster(allowlist, monkeypatch):
    from webapp.services import reporting_repository
    calls = []

    def email_map(self, token):
        calls.append(token)
        if token == "boom":
            raise reporting_repository.ReportingGraphError("user_information", 503)
        return dict(ROSTER)
    monkeypatch.setattr(reporting_repository.ReportingRepository, "get_colaboradores_email_map", email_map)
    return calls


def catalog(c, token="base-read-token", headers=ORIGIN):
    return c.post("/cchh/candidatos/compartir/catalogo", data={"graph_token": token}, headers=headers)


def test_catalog_enriches_only_allowlisted_identities_with_roster_names(roster):
    r = catalog(client())
    body = r.json()
    assert r.status_code == 200 and r.headers.get("cache-control", "").startswith("no-store")
    assert body["recipients"] == [{"name": "compacto.usuario@danwor.com", "email": "compacto.usuario@danwor.com"},   # fuera del padrón → correo
                                  {"name": "Helena Castro Ruiz", "email": HC}]                                        # del padrón → Nombre Apellido
    assert body["names_resolved"] == 1
    emails = [x["email"] for x in body["recipients"]]
    assert "colaborador.ajeno@danwor.com" not in emails and "otro.ajeno@danwor.com" not in emails                   # el padrón NUNCA amplía destinatarios
    assert "externo@gmail.com" not in emails
    assert roster == ["base-read-token"]                                                                              # una lectura por apertura, con el token base


def test_catalog_falls_back_to_email_when_sharepoint_fails_or_there_is_no_token(roster):
    for token in ("boom", ""):
        body = catalog(client(), token=token).json()
        assert [x["name"] for x in body["recipients"]] == [x["email"] for x in body["recipients"]] and body["names_resolved"] == 0
    assert roster == ["boom"]                                                                                         # sin token ni se consulta SharePoint


def test_catalog_requires_session_authorization_origin_and_the_allowlist_provider(roster, monkeypatch):
    assert catalog(TestClient(app)).status_code == 401
    assert catalog(client("otro@danwor.com")).status_code == 403
    assert catalog(client(), headers={"Origin": "https://evil.example"}).status_code == 403
    monkeypatch.setattr(config, "HC_SHARE_RECIPIENTS_PROVIDER", "directory", raising=False)
    assert catalog(client()).status_code == 404                                                                       # el proveedor definitivo no cambia
    assert roster == []


def test_names_are_presentation_only_send_still_validates_allowlist_membership(roster):
    a = candidate()
    r = send(client(), [a], [HC, "colaborador.ajeno@danwor.com"], graph_token="")
    body = r.json()
    assert body["status"] == "accepted"
    assert body["recipients"] == [HC] and body["invalid"] == ["colaborador.ajeno@danwor.com"]                        # estar en el padrón no habilita
    assert roster == []                                                                                               # el envío no consulta el padrón
