"""
test_datasphere_assessment.py — "Assessment de tu modelo" (exportación directa desde SAP Datasphere desde la UI del Hub).

Contratos que fija:
  * el panel "Herramientas de diagnóstico" del módulo 04 ofrece "Assessment de tu modelo" y "Diagnóstico conceptual" (árbol YAML de
    siempre, sin cambios); otros módulos no cambian;
  * ejecutar es SOLO POST (fetch, no navegación de formulario), mismo origen, sesión y dueño de la app; GET jamás ejecuta;
  * la web es un adaptador de `run_export` (la función del CLI): no arma comandos ni usa subprocess;
  * la salida del extractor viaja EN TIEMPO REAL, en orden y con su redacción original, a un panel LOCAL (no al loader del asistente);
  * el preflight OAuth corta ANTES de cualquier trabajo con el CLI; los mensajes PRECHECK_* del backend se muestran tal cual;
  * errores normalizados, single-flight, sin progreso falso, stdout como TEXTO;
  * el Client Secret nunca aparece en HTML, stream, logs, errores ni almacenamiento del navegador;
  * Spaces: configuración del Hub, prefijo esperado; el catálogo NO se regenera solo.
"""
from __future__ import annotations

import http.server
import json
import logging
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from webapp import access
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as svc

REPO = Path(__file__).resolve().parents[2]
SECRET = "S3cr3t-VALUE-xyz-987654"
STEM = "04_Modelado_y_Desarrollo"
RUN = f"/modules/{STEM}/datasphere-assessment/run"
REFRESH = f"/modules/{STEM}/datasphere-assessment/refresh-catalog"
ORIGIN = {"Origin": "http://testserver"}
HX = {"HX-Request": "true"}
VALID = {"host": "https://acme.eu10.hcs.cloud.sap", "token_url": "https://acme.authentication.eu10.hana.ondemand.com/oauth/token",
         "datasphere_client_id": "sb-client-id", "datasphere_client_secret": SECRET, "space": "ACME_SPACE", "analytic_model": "AM_VENTAS",
         "client": "Acme Corp"}


def _client(email="owner@danwor.com"):
    session = auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"})
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def _events(r):
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _done(r):
    return next(e for e in _events(r) if e["t"] == "done")


def _lines(r):
    return [e["v"] for e in _events(r) if e["t"] == "line"]


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    svc._RUNNING.clear()
    return _client("owner@danwor.com")


@pytest.fixture()
def fake_export(monkeypatch, tmp_path):
    """Sustituye SOLO la extracción real (CLI de Node) manteniendo `run_export` y su salida real (`_say`)."""
    calls = {"exports": 0, "cli": [], "extra_lines": []}
    monkeypatch.setattr(svc, "cli_available", lambda: True)
    monkeypatch.setattr(dsp.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    monkeypatch.setattr(dsp.DatasphereCli, "oauth_preflight", lambda self: (calls["cli"].append("oauth"), dsp._say("Preflight OAuth: OK (HTTP 200)")))
    monkeypatch.setattr(dsp.DatasphereCli, "login", lambda self: calls["cli"].append("login"))
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (calls["cli"].append("authz"), dsp._say(f"Preflight autorización: OK - acceso al Space {space}")))

    def run(self):
        calls["exports"] += 1
        for line in calls["extra_lines"]:
            dsp._say(line)
        d = self.output_dir / "analytic-models"
        d.mkdir(parents=True, exist_ok=True)
        (d / "AM_VENTAS.json").write_text("{}", encoding="utf-8")
        (self.output_dir / "views").mkdir(exist_ok=True)
        (self.output_dir / "views" / "V1.json").write_text("{}", encoding="utf-8")
        self.nodes = {("analytic-models", "AM_VENTAS"): dsp.Node("analytic-models", "AM_VENTAS"), ("views", "V1"): dsp.Node("views", "V1")}
        self.edges = {dsp.Edge("analytic-models", "AM_VENTAS", "reads", "views", "V1")}
        self.external_sources = {"SAPI.X"}
        return self.output_dir
    monkeypatch.setattr(dsp.LineageExporter, "run", run)
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda *a, **k: (_ for _ in ()).throw(AssertionError("la exportación no debe regenerar el catálogo")))
    calls["catalog_root"] = tmp_path / "catalog"
    return calls


def _post(c, data=None, **extra):
    return c.post(RUN, data=data or VALID, headers={**ORIGIN, **extra})


# ── A. selector de herramientas ─────────────────────────────────────────────
def test_diagnostic_panel_offers_the_two_tools_in_order_and_other_modules_are_unchanged(owner):
    r = owner.get(f"/modules/{STEM}?pack=datasphere_core")
    assert r.status_code == 200
    sel = re.search(r'<select id="diagnostic-tool-selector".*?</select>', r.text, re.S)
    assert sel
    assert re.findall(r"<option value=\"([^\"]*)\">([^<]*)</option>", sel.group(0)) == [
        ("", "Elegí una herramienta..."), ("assessment", "Assessment de tu modelo"), ("conceptual", "Diagnóstico conceptual")]
    assert 'id="diagnostic-selector"' in r.text and f'hx-post="/modules/{STEM}/trees/start"' in r.text
    other = owner.get("/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core")
    assert 'id="diagnostic-tool-selector"' not in other.text and "datasphere-assessment" not in other.text


def test_conceptual_diagnostic_fragment_is_unchanged_in_behavior(owner):
    r = owner.post(f"/modules/{STEM}/trees/start", data={"pack": "datasphere_core", "panel": "diagnostic", "tree_id": ""}, headers={**ORIGIN, **HX})
    assert r.status_code in (200, 404)


def test_conceptual_picker_respects_the_standard_margin_and_preselects_a_single_diagnostic(owner):
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core").text
    form = re.search(r'<form\s+class="tree-picker diagnostic-picker catalog-picker--secondary"[^>]*x-ref="conceptualForm".*?</form>', html, re.S)
    assert form
    assert len(re.findall(r"<option value=\"[^\"]+\"", form.group(0))) == 1 and re.search(r'<option value="[^"]+"\s+selected>', form.group(0))
    assert "autostart: true" in html and "$refs.conceptualForm.requestSubmit()" in html and 'hx-trigger="load"' not in html


def test_other_modules_keep_the_original_diagnostic_picker(owner):
    html = owner.get("/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core").text
    assert "conceptualForm" not in html


# ── B. formulario: credenciales sin pinta de login, async, secreto ─────────
def _form_html(owner):
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core").text
    start = html.index('<form id="dsa-form"')
    return html[start:html.index("</form>", start)]


def test_form_credentials_contract_reduces_browser_password_save_prompts(owner):
    form = _form_html(owner)
    tag = re.match(r"<form[^>]*>", form).group(0)
    assert 'autocomplete="off"' in tag and 'method="post"' in tag and f'action="{RUN}"' in tag
    cid = re.search(r'<input id="dsa-client_id"[^>]*>', form).group(0)
    for needle in ('type="text"', 'name="datasphere_client_id"', 'autocomplete="off"', 'autocapitalize="none"', 'spellcheck="false"'):
        assert needle in cid, needle
    sec = re.search(r'<input id="dsa-client_secret"[^>]*>', form).group(0)
    for needle in ('type="password"', 'name="datasphere_client_secret"', 'autocomplete="new-password"', 'autocapitalize="none"', 'spellcheck="false"'):
        assert needle in sec, needle
    assert "value=" not in sec and "value=" not in cid
    for generic in ('name="username"', 'name="user"', 'name="login"', 'name="password"', 'name="passwd"', 'name="secret"'):
        assert generic not in form


def test_submit_is_async_fetch_not_a_classic_navigation_nor_htmx(owner):
    form = _form_html(owner)
    assert not re.search(r"\bhx-", form)
    assert "data-dsa-form" in form
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert "event.preventDefault()" in js and "fetch(" in js and "XMLHttpRequest" not in js
    assert "datasphere_assessment.js" in (REPO / "webapp/templates/base.html").read_text(encoding="utf-8")


def test_form_fields_labels_placeholders_and_margins(owner):
    form = _form_html(owner)
    names = re.findall(r'<input id="dsa-[a-z_]+" name="([a-z_]+)" type="([a-z]+)"', form)
    assert names == [("host", "url"), ("token_url", "url"), ("datasphere_client_id", "text"), ("datasphere_client_secret", "password"),
                     ("space", "text"), ("analytic_model", "text"), ("client", "text")]
    labels = re.findall(r'<label class="tool-label" for="dsa-([a-z_]+)">([^<]+)</label>', form)
    assert [t for _n, t in labels] == ["URL del tenant", "Token URL (OAuth)", "Client ID", "Client Secret", "Space ID", "Modelo Analítico", "Nombre de Cliente"]
    for ph in ("AM_Compras", "Pampa", "sb-5d74c5f-XXXX", "5ff624c2-XXXX", "PRODUCCION"):
        assert f'placeholder="{ph}"' in form
    assert "admin-help" not in form and "style=" not in form
    for hidden in ("spaces", "bucket", "access key", "catalog_root", "csn", "mirror"):
        assert hidden not in form.lower()
    wrappers = re.findall(r'<div( class="catalog-picker--secondary")?>\s*<label class="tool-label" for="dsa-([a-z_]+)"', form)
    assert {n for cls, n in wrappers if not cls} == {"host", "space", "client"} and len(wrappers) == 7
    assert form.count('class="tree-picker catalog-picker--secondary"') == 2
    assert re.search(r'<div class="catalog-picker--secondary">\s*<span class="admin-result-title">Destino</span>', form)


def test_non_owner_sees_the_tool_but_no_execution_form(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    html = _client("someone@danwor.com").get(f"/modules/{STEM}?pack=datasphere_core").text
    assert "Acceso restringido" in html and 'id="dsa-form"' not in html


def test_validation_rejects_bad_input_and_never_echoes_the_secret(owner, fake_export):
    bad = dict(VALID, host="http://evil.example.com", token_url="https://evil.example.com/oauth/token", space="a b", client="")
    r = _post(owner, bad)
    d = _done(r)
    assert d["state"] == "INVALID" and set(d["errors"]) >= {"host", "token_url", "space", "client"}
    assert fake_export["exports"] == 0 and not fake_export["cli"] and SECRET not in r.text
    d2 = _done(_post(owner, dict(VALID, datasphere_client_secret="")))
    assert "Ingresá el Client Secret" in d2["errors"]["client_secret"]


# ── C. seguridad ────────────────────────────────────────────────────────────
def test_get_never_executes_and_post_requires_session_origin_and_owner(owner, fake_export, monkeypatch):
    assert owner.get(RUN).status_code == 405 and owner.get(REFRESH).status_code == 405
    assert TestClient(main.app).post(RUN, data=VALID, headers=ORIGIN).status_code == 401
    assert owner.post(RUN, data=VALID).status_code == 403
    assert owner.post(RUN, data=VALID, headers={"Origin": "https://evil.example"}).status_code == 403
    monkeypatch.setattr(access, "APP_OWNER", "other@danwor.com")
    assert _post(owner).status_code == 403
    assert owner.post("/modules/01_Arquitectura_y_Conectividad/datasphere-assessment/run", data=VALID, headers=ORIGIN).status_code == 404
    assert fake_export["exports"] == 0


def test_secret_never_appears_in_stream_urls_logs_activity_or_browser_storage(owner, fake_export, caplog, monkeypatch):
    seen = []
    import webapp.routers.datasphere_assessment as router_mod
    monkeypatch.setattr(router_mod, "log_activity", lambda *a, **k: seen.append((a, k)))
    fake_export["extra_lines"] = [f"eco accidental {SECRET}", "Authorization: Basic QUJDREVGR0g="]
    with caplog.at_level(logging.DEBUG):
        ok = _post(owner)
        monkeypatch.setattr(dsp.DatasphereCli, "oauth_preflight",
                            lambda self: (_ for _ in ()).throw(dsp.DatasphereCliError(f"PRECHECK_OAUTH_401 - rechazado {self.client_secret} Bearer abcdefghijklmnop")))
        bad = _post(owner)
    for r in (ok, bad):
        assert r.status_code == 200 and SECRET not in r.text and "abcdefghijklmnop" not in r.text and SECRET not in str(r.request.url)
    assert SECRET not in caplog.text and "abcdefghijklmnop" not in caplog.text and SECRET not in repr(seen)
    assert "client_secret" not in svc.FormValues.__dataclass_fields__ and "datasphere_client_secret" not in svc.FormValues.__dataclass_fields__
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert not re.search(r"localStorage|sessionStorage|indexedDB|document\.cookie", js)
    assert js.index('input[type="password"]') < js.index("await fetch(") and 'i.value = ""' in js
    tpl = (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")
    assert "value=" not in re.search(r'<input id="dsa-\{\{ name \}\}"[^>]*>', tpl, re.S).group(0)


def test_sanitize_redacts_secret_tokens_basic_auth_and_internal_paths():
    import base64
    basic = base64.b64encode(f"id:{SECRET}".encode()).decode()
    out = svc.sanitize(f"x {SECRET} Authorization: Basic {basic} Bearer eyJabc.def.ghi C:/Users/Joe/app/catalog_local/x", (SECRET,), {"C:/Users/Joe/app": "<app>"})
    assert SECRET not in out and basic not in out and "eyJabc" not in out and "<app>" in out


# ── D. adaptador ────────────────────────────────────────────────────────────
def test_web_layer_has_no_subprocess_or_cli_command_construction():
    for rel in ("webapp/routers/datasphere_assessment.py", "webapp/services/datasphere_assessment_service.py",
                "webapp/templates/partials/datasphere_assessment_tool.html", "webapp/templates/partials/diagnostic_tab.html",
                "webapp/static/datasphere_assessment.js"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert not re.search(r"\bsubprocess\b|\bos\.system\b|powershell|--client-secret|requests\.|urllib\.request|http\.client|Popen", src), rel
    assert "DSP_CLI_Export_Lineage" in (REPO / "webapp/services/datasphere_assessment_service.py").read_text(encoding="utf-8")


def test_web_passes_the_hub_contract_to_run_export(owner, monkeypatch, fake_export):
    seen = {}
    monkeypatch.setattr(dsp, "run_export", lambda req: seen.setdefault("req", req) and dsp.ExportResult(ok=True, client=req.client, space=req.space, analytic_model=req.analytic_model))
    from core import remote_store
    monkeypatch.setattr(remote_store, "enabled", lambda: True)
    _post(owner)
    req = seen["req"]
    assert (req.host, req.token_url, req.client_id, req.client_secret) == (VALID["host"], VALID["token_url"], VALID["datasphere_client_id"], SECRET)
    assert (req.space, req.analytic_model, req.client) == ("ACME_SPACE", "AM_VENTAS", "Acme Corp")
    assert req.generate_catalog is False and req.mirror_up is True and req.catalog_root is None and req.csn_cache_dir is None
    assert "client_secret" not in repr(req)


def test_oauth_preflight_401_stops_before_any_cli_work_and_200_continues(monkeypatch, tmp_path):
    status = {"code": 401}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(status["code"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"access_token":"tok-123"}' if status["code"] == 200 else b'{"error":"invalid_client"}')

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv(svc.ALLOWED_HOSTS_ENV, "127.0.0.1")
        monkeypatch.setattr(svc, "cli_available", lambda: True)
        monkeypatch.setattr(dsp.shutil, "which", lambda n: "/usr/bin/npx")
        monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "cat"))
        runs = []
        monkeypatch.setattr(dsp.DatasphereCli, "_run", lambda self, args, expect_json=False: runs.append(args[0]) or ([] if expect_json else ""))
        monkeypatch.setattr(dsp.LineageExporter, "run", lambda self: (self.output_dir.mkdir(parents=True, exist_ok=True), self.output_dir)[1])
        url = f"http://127.0.0.1:{srv.server_port}/oauth/token"
        vals = svc.FormValues(host=url.rsplit("/oauth", 1)[0], token_url=url, client_id="id", space="S", analytic_model="AM", client="C")
        out = svc.run(vals, SECRET)
        assert out.state == "FAILED" and out.error_code == "AUTHENTICATION_FAILED" and runs == []
        assert out.message.startswith("PRECHECK_OAUTH_401") and SECRET not in " ".join(out.trace)
        status["code"] = 200
        out2 = svc.run(vals, SECRET)
        assert out2.state == "SUCCESS" and runs and runs[0] == "login"
    finally:
        srv.shutdown()


# ── E. ejecución y avance local ─────────────────────────────────────────────
def test_stdout_lines_stream_in_order_with_original_wording_and_end_with_success(owner, fake_export, monkeypatch):
    fake_export["extra_lines"] = ["Inventory analytic-models: 11", "Inventory views: 89", "[RF 12/38] RF_MD_REGUH", "OK - target(s): X"]
    from core import remote_store
    monkeypatch.setattr(remote_store, "enabled", lambda: True)
    monkeypatch.setattr(remote_store, "mirror_up", lambda: 3)
    r = _post(owner)
    events = _events(r)
    assert events[0] == {"t": "start"} and events[-1]["t"] == "done"
    lines = _lines(r)
    wanted = ["Preflight OAuth: OK (HTTP 200)", "Preflight autorización: OK - acceso al Space ACME_SPACE", "Inventory analytic-models: 11",
              "Inventory views: 89", "[RF 12/38] RF_MD_REGUH", "OK - target(s): X"]
    positions = [lines.index(w) for w in wanted]
    assert positions == sorted(positions)
    d = _done(r)
    assert d["state"] == "SUCCESS" and d["error_code"] is None
    rows = dict(map(tuple, d["rows"]))
    assert rows["Cliente"] == "Acme Corp" and rows["Space"] == "ACME_SPACE" and rows["Archivos exportados"] == "2" and rows["Objetos incluidos"] == "2"
    assert rows["Persistencia en Spaces"].startswith("3 archivos sincronizados") and rows["Destino (pool del catálogo)"] == "catalog_local/.csn_cache/Acme_Corp/"
    assert fake_export["cli"] == ["oauth", "login", "authz"] and fake_export["exports"] == 1
    assert (fake_export["catalog_root"] / ".csn_cache" / "Acme_Corp" / "analytic-models" / "AM_VENTAS.json").is_file()
    monkeypatch.setattr(remote_store, "enabled", lambda: False)
    assert "No configurada en este entorno" in dict(map(tuple, _done(_post(owner))["rows"]))["Persistencia en Spaces"]


def test_lines_are_delivered_while_the_extraction_is_still_running(fake_export, monkeypatch):
    """Tiempo real: el callback recibe la línea ANTES de que termine la extracción (no se bufferiza hasta el final)."""
    gate, entered = threading.Event(), threading.Event()
    got = []
    orig = dsp.LineageExporter.run

    def slow(self):
        dsp._say("primera línea en vivo")
        entered.set()
        assert gate.wait(5)
        return orig(self)
    monkeypatch.setattr(dsp.LineageExporter, "run", slow)
    vals = svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", analytic_model="AM", client="C")
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("out", svc.run(vals, SECRET, on_line=got.append)))
    t.start()
    assert entered.wait(5)
    assert "primera línea en vivo" in got and t.is_alive() and "out" not in box
    gate.set()
    t.join(5)
    assert box["out"].state == "SUCCESS" and got.index("primera línea en vivo") < len(got) - 1


@pytest.mark.parametrize("message,code,backend_text", [
    ("PRECHECK_OAUTH_401 - OAuth rechazó las credenciales (HTTP 401). Revisá Client ID.", "AUTHENTICATION_FAILED", True),
    ("PRECHECK_AUTH_403 - OAuth válido, pero el Technical User no tiene permisos suficientes.", "INSUFFICIENT_DATASPHERE_PRIVILEGES", True),
    ("PRECHECK_OAUTH_NETWORK_ERROR - No se pudo contactar el endpoint OAuth.", "OAUTH_ENDPOINT_UNREACHABLE", True),
    ("PRECHECK_AUTH_ERROR - No se pudo validar acceso al Space.", "SPACE_ACCESS_FAILED", True),
    ("boom", "EXTRACTION_FAILED", False)])
def test_backend_messages_are_preserved_and_failures_are_normalized(owner, fake_export, monkeypatch, message, code, backend_text):
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError(message)))
    r = _post(owner)
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == code and fake_export["exports"] == 0
    if backend_text:
        assert d["message"] == message
    else:
        assert d["message"] == svc.ERROR_MESSAGES[code] and "boom" not in d["message"]
    assert any(message in line for line in _lines(r))
    assert "Traceback" not in r.text and SECRET not in r.text
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert not re.search(r"PRECHECK_|401|403", js)


def test_cli_unavailable_and_storage_failures_have_their_own_codes(owner, fake_export, monkeypatch):
    monkeypatch.setattr(svc, "cli_available", lambda: False)
    assert _done(_post(owner))["error_code"] == "DATASPHERE_CLI_UNAVAILABLE"
    monkeypatch.setattr(svc, "cli_available", lambda: True)
    from core import remote_store
    monkeypatch.setattr(remote_store, "enabled", lambda: True)
    monkeypatch.setattr(remote_store, "mirror_up", lambda: (_ for _ in ()).throw(RuntimeError(SECRET)))
    r = _post(owner)
    assert _done(r)["error_code"] == "STORAGE_UPLOAD_FAILED" and SECRET not in r.text


def test_stdout_with_html_like_text_travels_as_data_and_is_inserted_as_text(owner, fake_export):
    fake_export["extra_lines"] = ["<script>alert(1)</script> <img src=x onerror=alert(2)>"]
    r = _post(owner)
    assert "<script>alert(1)</script> <img src=x onerror=alert(2)>" in _lines(r)
    assert r.headers["content-type"].startswith("application/x-ndjson")
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", js) and "textContent" in js
    assert "|safe" not in (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")


def test_duplicate_execution_for_the_same_operation_is_rejected_truthfully(fake_export, monkeypatch):
    svc._RUNNING.clear()
    gate, entered = threading.Event(), threading.Event()
    orig = dsp.LineageExporter.run

    def slow(self):
        entered.set()
        gate.wait(5)
        return orig(self)
    monkeypatch.setattr(dsp.LineageExporter, "run", slow)
    vals = svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", analytic_model="AM", client="C")
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("first", svc.run(vals, SECRET)))
    t.start()
    assert entered.wait(5)
    dup = svc.run(vals, SECRET)
    assert dup.state == "FAILED" and dup.error_code == "OPERATION_ALREADY_RUNNING"
    gate.set()
    t.join(5)
    assert box["first"].state == "SUCCESS" and svc.run(vals, SECRET).state == "SUCCESS"
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert "submit.disabled" in js and "aria-busy" in js


# ── H. desacople del loader global del asistente ────────────────────────────
def test_datasphere_execution_does_not_touch_the_assistant_global_loader(owner):
    tpl = (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    run_form = tpl[tpl.index('<form id="dsa-form"'):tpl.index("</form>", tpl.index('<form id="dsa-form"'))]
    assert not re.search(r"section-analysis-form|assistant-chat-form|assistant-route-explain|assistant-request-loading|section-analysis-loading|htmx-indicator|hx-indicator", run_form)
    assert "Obteniendo la mejor respuesta" not in tpl + js and not re.search(r"htmx|assistant|section-analysis|is-loading", js)
    app_js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert "data-dsa" not in app_js and "#dsa-" not in app_js and "datasphere_assessment" not in app_js
    assert 'data-running-text="Ejecutando extracción..."' in tpl and "data-dsa-progress" in tpl and "data-dsa-log" in tpl
    assert not re.search(r"\d+\s*%|<progress|role=\"progressbar\"", tpl + js)
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core").text
    panel = html[html.index('id="dsa-panel"'):html.index("</form>", html.index('id="dsa-form"'))]
    assert "assistant-request-loading" not in panel and "Obteniendo la mejor respuesta" not in panel


def test_local_progress_panel_uses_hub_primitives_for_status_and_technical_detail(owner):
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core").text
    panel = html[html.index("data-dsa-progress"):]
    panel = panel[:panel.index('id="dsa-regen"')]
    for primitive in ("callout callout--success", "callout callout--warn", 'details class="admin-tool"', "admin-tool__summary", "admin-result-output", "Detalle técnico"):
        assert primitive in panel, primitive
    assert "style=" not in panel and "<progress" not in panel


# ── F. almacenamiento ───────────────────────────────────────────────────────
def test_spaces_prefix_matches_the_hub_storage_convention_and_uses_hub_config(owner, fake_export, monkeypatch):
    from core import remote_store
    monkeypatch.setattr(remote_store, "enabled", lambda: True)
    monkeypatch.setattr(remote_store, "mirror_up", lambda: 1)
    monkeypatch.setattr(config, "SPACES_PREFIX", "prod")
    rows = dict(map(tuple, _done(_post(owner))["rows"]))
    expected = remote_store._key("catalog_local/.csn_cache/Acme_Corp") + "/"
    assert expected == "prod/catalog_local/.csn_cache/Acme_Corp/" and expected in rows["Persistencia en Spaces"]
    assert "catalog_local" in config.PERSIST_PATHS
    for rel in ("webapp/services/datasphere_assessment_service.py", "webapp/routers/datasphere_assessment.py"):
        assert not re.search(r"boto3|SPACES_KEY|SPACES_SECRET|SPACES_BUCKET", (REPO / rel).read_text(encoding="utf-8")), rel


# ── G. cadena con el Catálogo ───────────────────────────────────────────────
def test_export_never_regenerates_the_catalog_and_refresh_is_an_explicit_shared_step(owner, fake_export, monkeypatch):
    _post(owner)
    assert fake_export["exports"] == 1
    seen = {}
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda csn, cat: seen.update(csn=csn, cat=cat) or "resumen")
    cleared = []
    monkeypatch.setattr(main, "_invalidate_catalog_cache", lambda: cleared.append(1))
    r = owner.post(REFRESH, data={"pack": "datasphere_core"}, headers={**ORIGIN, **HX})
    assert r.status_code == 200 and "Catálogo actualizado" in r.text and cleared == [1]
    assert seen["csn"].name == ".csn_cache" and seen["cat"] == fake_export["catalog_root"].resolve()
    assert owner.post(REFRESH, data={"pack": "datasphere_core"}, headers=HX).status_code == 403


def test_export_invalidates_the_assessment_pool_index(owner, fake_export, monkeypatch):
    from webapp.services import assessment_service
    called = []
    monkeypatch.setattr(assessment_service, "invalidate_pool_index", lambda: called.append(1))
    _post(owner)
    assert called == [1]


# ── regresión: el script sigue siendo un CLI ───────────────────────────────
def test_cli_adapter_and_web_adapter_share_one_implementation():
    src = (REPO / "tools/catalog/DSP_CLI_Export_Lineage.py").read_text(encoding="utf-8")
    assert "def run_export(" in src and src.count("def main(") == 1 and "run_export(ExportRequest(" in src
    assert "--client-secret" in src and "--mirror-up" in src and "--no-generate-catalog" in src and "repr=False" in src
    assert dsp.classify_error("PRECHECK_OAUTH_401 - x") == "AUTHENTICATION_FAILED" and dsp.classify_error("otra") == "EXTRACTION_FAILED"


def test_capture_output_streams_per_line_and_does_not_touch_global_stdout(capsys):
    seen = []
    with dsp.capture_output(on_line=seen.append) as cap:
        dsp._say("hola")
        dsp._say("a", "b")
    dsp._say("fuera")
    out = capsys.readouterr().out
    assert cap.lines == ["hola", "a b"] and seen == ["hola", "a b"] and "fuera" in out and "hola" not in out


def test_cli_still_prints_exactly_like_print_without_a_sink(capsys):
    import sys
    dsp._say("Preflight OAuth: OK (HTTP 200)")
    dsp._say("x", "y", file=sys.stderr)
    cap = capsys.readouterr()
    assert cap.out == "Preflight OAuth: OK (HTTP 200)\n" and cap.err == "x y\n"
