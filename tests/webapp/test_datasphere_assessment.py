"""
test_datasphere_assessment.py — "Assessment de tu modelo": assessment AD-HOC desde SAP Datasphere (evaluar != publicar).

Contratos que fija:
  * el panel "Herramientas de diagnóstico" del módulo 04 ofrece "Assessment de tu modelo" y "Diagnóstico conceptual";
  * ejecutar es SOLO POST (fetch), mismo origen, sesión y dueño; GET jamás ejecuta; el stdout viaja en vivo a un panel LOCAL;
  * la web es un adaptador: `run_export` (misma función que el CLI) → `assess_evidence_root` (MISMO motor) → resultado ad-hoc;
  * NADA canónico se toca: ni `.csn_cache`, ni `.lineage_runs`, ni Spaces, ni el Catálogo, ni la caché de assessment del Catálogo;
  * workspace por operación fuera de todo directorio persistido (fail-closed), borrado en éxito y en falla;
  * resultado bajo `result_id` opaco, 24 h, y el MISMO informe con contexto ad-hoc (sin linaje, sin regenerar);
  * el Client Secret nunca aparece en HTML, stream, resultado, exportaciones, logs ni almacenamiento del navegador.
"""
from __future__ import annotations

import http.server
import json
import logging
import re
import shutil
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from webapp import access
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as asvc
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as svc

REPO = Path(__file__).resolve().parents[2]
SECRET = "S3cr3t-VALUE-xyz-987654"
STEM = "04_Modelado_y_Desarrollo"
RUN = f"/modules/{STEM}/datasphere-assessment/run"
REFRESH = f"/modules/{STEM}/datasphere-assessment/refresh-catalog"
ORIGIN = {"Origin": "http://testserver"}
HX = {"HX-Request": "true"}
VALID = {"pack": "datasphere_core", "host": "https://acme.eu10.hcs.cloud.sap",
         "token_url": "https://acme.authentication.eu10.hana.ondemand.com/oauth/token",
         "datasphere_client_id": "sb-client-id", "datasphere_client_secret": SECRET, "space": "ACME_SPACE",
         "analytic_model": "4AM_ACME", "client": "Acme"}


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


def _phases(r):
    return [e["v"] for e in _events(r) if e["t"] == "phase"]


def _tree(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    svc._RUNNING.clear()
    return _client("owner@danwor.com")


@pytest.fixture()
def fake_export(monkeypatch, tmp_path):
    """Sustituye SOLO la extracción real (CLI de Node): copia evidencia sintética REAL al directorio de salida que pide el extractor.
    `run_export`, el motor de assessment y el almacén ad-hoc son los reales."""
    from core import remote_store
    calls = {"exports": 0, "cli": [], "extra_lines": [], "requests": [], "out_dirs": []}
    cache = tmp_path / "assessment_cache"
    canonical = tmp_path / "canonical_catalog"
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", cache)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(canonical))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    svc.reset_boot_state()
    asvc.reset_memory_cache()
    evidence = write_inbound(tmp_path / "evidence_src")
    monkeypatch.setattr(svc, "cli_available", lambda: True)
    monkeypatch.setattr(dsp.shutil, "which", lambda name: "/usr/bin/npx")
    monkeypatch.setattr(dsp.DatasphereCli, "oauth_preflight", lambda self: (calls["cli"].append("oauth"), dsp._say("Preflight OAuth: OK (HTTP 200)")))
    monkeypatch.setattr(dsp.DatasphereCli, "login", lambda self: calls["cli"].append("login"))
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (calls["cli"].append("authz"), dsp._say(f"Preflight autorización: OK - acceso al Space {space}")))

    def run(self):
        calls["exports"] += 1
        calls["out_dirs"].append(self.output_dir)
        for line in calls["extra_lines"]:
            dsp._say(line)
        shutil.copytree(evidence / "Acme", self.output_dir, dirs_exist_ok=True)
        self.nodes = {("analytic-models", "4AM_ACME"): dsp.Node("analytic-models", "4AM_ACME"), ("views", "3VF_P"): dsp.Node("views", "3VF_P")}
        self.edges = {dsp.Edge("analytic-models", "4AM_ACME", "reads", "views", "3VF_P")}
        self.external_sources = {"SAPI.X"}
        return self.output_dir
    monkeypatch.setattr(dsp.LineageExporter, "run", run)
    orig_export = dsp.run_export
    monkeypatch.setattr(dsp, "run_export", lambda req: (calls["requests"].append(req), orig_export(req))[1])
    # nada de publicación: si algo llama a estos, el test falla
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda *a, **k: (_ for _ in ()).throw(AssertionError("generate_all no debe correr")))
    monkeypatch.setattr(remote_store, "enabled", lambda: True)
    monkeypatch.setattr(remote_store, "mirror_up", lambda *a, **k: (_ for _ in ()).throw(AssertionError("mirror_up no debe correr")))
    calls.update(cache=cache, canonical=canonical, evidence=evidence)
    return calls


def _post(c, data=None, **extra):
    return c.post(RUN, data=data or VALID, headers={**ORIGIN, **extra})


def _rid(r) -> str:
    return re.search(r"/result/([0-9a-f]{32})", _done(r)["result_url"]).group(1)


# ── A. selector de herramientas ─────────────────────────────────────────────
def test_diagnostic_panel_offers_the_two_tools_in_order_and_other_modules_are_unchanged(owner):
    r = owner.get(f"/modules/{STEM}?pack=datasphere_core")
    assert r.status_code == 200
    sel = re.search(r'<select id="diagnostic-tool-selector".*?</select>', r.text, re.S)
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
    assert form and re.search(r'<option value="[^"]+"\s+selected>', form.group(0))
    assert "autostart: true" in html and 'hx-trigger="load"' not in html


# ── B. formulario ───────────────────────────────────────────────────────────
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
    assert "value=" not in sec and "value=" not in cid and 'name="pack"' in form


def test_submit_is_async_fetch_not_a_classic_navigation_nor_htmx(owner):
    form = _form_html(owner)
    assert not re.search(r"\bhx-", form) and "data-dsa-form" in form
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
    assert "admin-help" not in form and "style=" not in form
    assert form.count('class="tree-picker catalog-picker--secondary"') == 2


def test_non_owner_sees_the_tool_but_no_execution_form(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    html = _client("someone@danwor.com").get(f"/modules/{STEM}?pack=datasphere_core").text
    assert "Acceso restringido" in html and 'id="dsa-form"' not in html


def test_validation_rejects_bad_input_and_never_echoes_the_secret(owner, fake_export):
    bad = dict(VALID, host="http://evil.example.com", token_url="https://evil.example.com/oauth/token", space="a b", client="")
    r = _post(owner, bad)
    d = _done(r)
    assert d["state"] == "INVALID" and set(d["errors"]) >= {"host", "token_url", "space", "client"}
    assert fake_export["exports"] == 0 and not fake_export["cli"] and SECRET not in r.text and d["result_url"] is None
    assert "Ingresá el Client Secret" in _done(_post(owner, dict(VALID, datasphere_client_secret=""))) ["errors"]["client_secret"]


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


def test_secret_never_appears_in_stream_urls_logs_results_exports_or_browser_storage(owner, fake_export, caplog, monkeypatch):
    seen = []
    import webapp.routers.datasphere_assessment as router_mod
    monkeypatch.setattr(router_mod, "log_activity", lambda *a, **k: seen.append((a, k)))
    fake_export["extra_lines"] = [f"eco accidental {SECRET}", "Authorization: Basic QUJDREVGR0g="]
    with caplog.at_level(logging.DEBUG):
        ok = _post(owner)
        rid = _rid(ok)
        report = owner.get(f"/modules/{STEM}/datasphere-assessment/result/{rid}")
        js_export = owner.get(f"/modules/{STEM}/datasphere-assessment/result/{rid}/export.json")
        md_export = owner.get(f"/modules/{STEM}/datasphere-assessment/result/{rid}/export.md")
        monkeypatch.setattr(dsp.DatasphereCli, "oauth_preflight",
                            lambda self: (_ for _ in ()).throw(dsp.DatasphereCliError(f"PRECHECK_OAUTH_401 - rechazado {self.client_secret} Bearer abcdefghijklmnop")))
        bad = _post(owner)
    for r in (ok, bad, report, js_export, md_export):
        assert r.status_code == 200 and SECRET not in r.text and "abcdefghijklmnop" not in r.text and SECRET not in str(r.request.url)
    stored = "".join(p.read_text(encoding="utf-8") for p in (fake_export["cache"] / "adhoc").rglob("*.json"))
    assert stored and SECRET not in stored and "client_secret" not in stored and "access_token" not in stored       # el almacén no guarda credenciales
    assert SECRET not in caplog.text and "abcdefghijklmnop" not in caplog.text and SECRET not in repr(seen)
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert not re.search(r"localStorage|sessionStorage|indexedDB|document\.cookie", js)
    assert js.index('input[type="password"]') < js.index("await fetch(") and 'i.value = ""' in js


def test_sanitize_redacts_secret_tokens_basic_auth_and_internal_paths():
    import base64
    basic = base64.b64encode(f"id:{SECRET}".encode()).decode()
    out = svc.sanitize(f"x {SECRET} Authorization: Basic {basic} Bearer eyJabc.def.ghi C:/Users/Joe/app/catalog_local/x", (SECRET,), {"C:/Users/Joe/app": "<app>"})
    assert SECRET not in out and basic not in out and "eyJabc" not in out and "<app>" in out


# ── D. adaptador: mismo extractor, mismo motor ─────────────────────────────
def test_web_layer_has_no_subprocess_or_cli_command_construction():
    for rel in ("webapp/routers/datasphere_assessment.py", "webapp/services/datasphere_assessment_service.py",
                "webapp/templates/partials/datasphere_assessment_tool.html", "webapp/templates/partials/diagnostic_tab.html",
                "webapp/static/datasphere_assessment.js"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert not re.search(r"\bsubprocess\b|\bos\.system\b|powershell|--client-secret|requests\.|urllib\.request|http\.client|Popen", src), rel
    assert "DSP_CLI_Export_Lineage" in (REPO / "webapp/services/datasphere_assessment_service.py").read_text(encoding="utf-8")


def test_real_oauth_preflight_401_stops_before_any_cli_work_and_200_continues(monkeypatch, tmp_path):
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
        monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "cache")
        monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "canonical"))
        svc.reset_boot_state()
        monkeypatch.setattr(svc, "cli_available", lambda: True)
        monkeypatch.setattr(dsp.shutil, "which", lambda n: "/usr/bin/npx")
        runs = []
        monkeypatch.setattr(dsp.DatasphereCli, "_run", lambda self, args, expect_json=False: runs.append(args[0]) or ([] if expect_json else ""))
        monkeypatch.setattr(dsp.LineageExporter, "run", lambda self: (self.output_dir.mkdir(parents=True, exist_ok=True), self.output_dir)[1])
        url = f"http://127.0.0.1:{srv.server_port}/oauth/token"
        vals = svc.FormValues(host=url.rsplit("/oauth", 1)[0], token_url=url, client_id="id", space="S", analytic_model="4AM_ACME", client="Acme")
        out = svc.run(vals, SECRET)
        assert out.state == "FAILED" and out.error_code == "AUTHENTICATION_FAILED" and out.stage == "extraction" and runs == []
        assert out.message.startswith("PRECHECK_OAUTH_401") and SECRET not in " ".join(out.trace)
        status["code"] = 200
        out2 = svc.run(vals, SECRET)
        assert runs and runs[0] == "login" and out2.stage in ("assessment", "extraction")                 # 200 continúa con login + autorización
    finally:
        srv.shutdown()


# ── E. NADA se publica / workspace temporal ────────────────────────────────
def test_adhoc_run_does_not_publish_and_leaves_canonical_state_untouched(owner, fake_export):
    canonical = fake_export["canonical"]
    before = _tree(canonical)
    r = _post(owner)
    assert _done(r)["state"] == "SUCCESS"
    assert _tree(canonical) == before == []                                           # ni .csn_cache ni .lineage_runs ni catálogo
    assert not (canonical / ".csn_cache").exists() and not (canonical / ".lineage_runs").exists()
    req = fake_export["requests"][0]
    assert req.generate_catalog is False and req.mirror_up is False                    # sin generate_all ni Spaces (los stubs lanzarían)
    assert asvc._memory == {}                                                           # la caché de assessment del Catálogo no se tocó


def test_extractor_receives_operation_scoped_roots_outside_every_persisted_path(owner, fake_export):
    from core import remote_store
    _post(owner)
    req = fake_export["requests"][0]
    cache = fake_export["cache"]
    assert req.catalog_root is not None and req.csn_cache_dir is not None
    assert req.catalog_root.parent == req.csn_cache_dir.parent and req.catalog_root.parent.parent == cache / "adhoc" / "work"
    for root in (req.catalog_root, req.csn_cache_dir):
        assert not any(str(root).startswith(str(p)) for _n, p in remote_store._persist_dirs())
    assert fake_export["out_dirs"][0] == req.csn_cache_dir / "Acme"                      # carpeta del cliente (no el nombre de pantalla)


def test_adhoc_paths_fail_closed_when_they_would_resolve_under_a_persisted_root(owner, fake_export, monkeypatch):
    from core import remote_store
    persisted = remote_store._persist_dirs()[0][1]
    for bad in (persisted / "assessment_cache", Path(fake_export["canonical"]) / "x"):
        monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", bad)
        monkeypatch.setenv("DANWOR_CATALOG_DIR", str(bad.parent if bad.parent != persisted else persisted))
        svc.reset_boot_state()
        with pytest.raises(svc.AdhocStorageError):
            svc.adhoc_base()
        d = _done(_post(owner))
        assert d["state"] == "FAILED" and d["error_code"] == "ADHOC_STORAGE_UNSAFE" and d["result_url"] is None
    assert fake_export["exports"] == 0                                                   # fail-closed: ni se ejecutó el extractor
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", fake_export["cache"])
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(fake_export["canonical"]))
    svc.reset_boot_state()
    assert svc.adhoc_base() == fake_export["cache"] / "adhoc"


def test_temporary_evidence_is_removed_after_success_and_after_assessment_failure(owner, fake_export, monkeypatch):
    work = fake_export["cache"] / "adhoc" / "work"
    assert _done(_post(owner))["state"] == "SUCCESS"
    assert list(work.iterdir()) == []                                                    # éxito: perfil guardado, evidencia borrada
    monkeypatch.setattr(asvc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("motor roto")))
    d = _done(_post(owner))
    assert d["state"] == "FAILED" and list(work.iterdir()) == []                         # falla de assessment: también limpia
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError("PRECHECK_AUTH_403 - x")))
    assert _done(_post(owner))["state"] == "FAILED" and list(work.iterdir()) == []       # falla de extracción: también limpia


# ── F. orquestación automática ─────────────────────────────────────────────
def test_extraction_success_automatically_runs_the_same_engine_for_exactly_the_requested_am(owner, fake_export, monkeypatch):
    from tools.catalog.modeling_assessment import shadow as ma_shadow
    seen = []
    real = ma_shadow.assess
    monkeypatch.setattr(ma_shadow, "assess", lambda inbound, client, am, domain=None: (seen.append((Path(inbound), client, am, domain)), real(inbound, client, am, domain))[1])
    r = _post(owner)
    d = _done(r)
    assert d["state"] == "SUCCESS" and d["result_url"] and fake_export["exports"] == 1
    (inbound, client_dir, am, domain), = seen
    assert client_dir == "Acme" and am == "4AM_ACME" and domain == "General"             # carpeta real del cliente, AM explícito
    assert inbound.parent.parent == fake_export["cache"] / "adhoc" / "work"
    rid = _rid(r)
    rec = svc.load_result(rid)
    assert rec["terminal_am"] == "4AM_ACME" and "4AM_ACME" in json.dumps(rec["profile"]) and "4AM_OTHER" not in json.dumps(rec["profile"])   # NO se evaluó 4AM_OTHER (también estaba en la evidencia)
    assert rec["client_dir"] == "Acme" and rec["client_display"] == "Acme"


def test_progress_phases_are_truthful_and_stdout_streams_in_order(owner, fake_export):
    fake_export["extra_lines"] = ["Inventory analytic-models: 11", "[RF 12/38] RF_MD_REGUH"]
    r = _post(owner)
    events = _events(r)
    assert events[0] == {"t": "start"} and events[-1]["t"] == "done"
    lines = _lines(r)
    wanted = ["Preflight OAuth: OK (HTTP 200)", "Preflight autorización: OK - acceso al Space ACME_SPACE", "Inventory analytic-models: 11", "[RF 12/38] RF_MD_REGUH"]
    positions = [lines.index(w) for w in wanted]
    assert positions == sorted(positions)
    assert _phases(r) == ["Evidencia adquirida", "Generando assessment...", "Assessment generado"]
    kinds = [e["t"] for e in events]
    assert kinds.index("phase") > max(i for i, k in enumerate(kinds) if k == "line" and events[i]["v"] in wanted)    # la fase llega DESPUÉS de la extracción
    assert not re.search(r"\d+\s*%", r.text.replace("100%", ""))


def test_lines_are_delivered_while_the_extraction_is_still_running(fake_export, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    got = []
    orig = dsp.LineageExporter.run

    def slow(self):
        dsp._say("primera línea en vivo")
        entered.set()
        assert gate.wait(5)
        return orig(self)
    monkeypatch.setattr(dsp.LineageExporter, "run", slow)
    vals = svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", analytic_model="4AM_ACME", client="Acme")
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("out", svc.run(vals, SECRET, on_line=got.append)))
    t.start()
    assert entered.wait(5)
    assert "primera línea en vivo" in got and t.is_alive() and "out" not in box
    gate.set()
    t.join(10)
    assert box["out"].state == "SUCCESS"


def test_extraction_failure_creates_no_result_and_is_labeled_as_extraction(owner, fake_export, monkeypatch):
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight",
                        lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError("PRECHECK_AUTH_403 - OAuth válido, pero sin permisos suficientes.")))
    r = _post(owner)
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == "INSUFFICIENT_DATASPHERE_PRIVILEGES" and d["title"] == "No se pudo completar la extracción"
    assert d["message"].startswith("PRECHECK_AUTH_403") and d["result_url"] is None and _phases(r) == []
    assert not list((fake_export["cache"] / "adhoc" / "results").glob("*.json"))


def test_assessment_failure_is_not_labeled_as_extraction_and_keeps_the_log(owner, fake_export, monkeypatch):
    monkeypatch.setattr(asvc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"boom {SECRET}")))
    r = _post(owner)
    d = _done(r)
    assert d["state"] == "FAILED" and d["title"] == "No se pudo generar el assessment" and d["error_code"] == "GENERATION_ERROR"
    assert d["message"].startswith("La evidencia se adquirió correctamente") and "boom" not in d["message"] and SECRET not in r.text
    assert _phases(r) == ["Evidencia adquirida", "Generando assessment..."] and _lines(r)                   # el detalle técnico sigue disponible
    assert d["result_url"] is None and not list((fake_export["cache"] / "adhoc" / "results").glob("*.json"))


def test_unknown_or_non_terminal_am_reports_an_assessment_stage_failure_with_future_safe_copy(owner, fake_export):
    r = _post(owner, dict(VALID, analytic_model="NO_EXISTE_AM"))
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == "AM_NOT_TERMINAL" and d["title"] == "No se pudo generar el assessment"
    assert d["message"] == ("La evidencia se adquirió correctamente, pero el artefacto indicado no puede evaluarse con el alcance actual. "
                            "Revisá el objeto seleccionado.")


def test_no_visible_failure_copy_hardcodes_analytic_model_or_terminal_language():
    for code, message in svc.ERROR_MESSAGES.items():
        assert not re.search(r"Analytic Model|terminal|scoring", message, re.I), code
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert not re.search(r"Analytic Model|terminal", js)


def test_phase_events_carry_spinner_semantics_one_row_four_phases(owner, fake_export):
    events = [e for e in _events(_post(owner)) if e["t"] == "phase"]
    assert [(e["phase"], e["v"]) for e in events] == [("evidence_acquired", "Evidencia adquirida"), ("assessing", "Generando assessment..."), ("assessment_ready", "Assessment generado")]
    assert all("spin" not in e for e in events)                                                   # la presentación no vive en el backend
    tpl = (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert tpl.count("section-analysis-loading-copy") == 1 and tpl.count("section-analysis-spinner") == 1       # UNA sola fila para las 4 fases
    assert "setHidden(spinner, false);" in js and "setHidden(spinner, !WORKING.has(event.phase))" in js and "setHidden(spinner, true)" in js
    assert 'new Set(["extracting", "assessing"])' in js and "spin" not in (REPO / "webapp/routers/datasphere_assessment.py").read_text(encoding="utf-8").replace("spinner", "")
    assert 'data-running-text="Ejecutando extracción..."' in tpl


def test_error_code_is_only_in_technical_detail_not_in_visible_status(owner, fake_export):
    tpl = (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert "log.textContent += " in js and "done.error_code" in js and "data-dsa-code" not in tpl + js


def test_duplicate_execution_for_the_same_operation_is_rejected_truthfully(fake_export, monkeypatch):
    svc._RUNNING.clear()
    gate, entered = threading.Event(), threading.Event()
    orig = dsp.LineageExporter.run

    def slow(self):
        entered.set()
        gate.wait(5)
        return orig(self)
    monkeypatch.setattr(dsp.LineageExporter, "run", slow)
    vals = svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", analytic_model="4AM_ACME", client="Acme")
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("first", svc.run(vals, SECRET)))
    t.start()
    assert entered.wait(5)
    dup = svc.run(vals, SECRET)
    assert dup.state == "FAILED" and dup.error_code == "OPERATION_ALREADY_RUNNING"
    gate.set()
    t.join(10)
    assert box["first"].state == "SUCCESS" and svc.run(vals, SECRET).state == "SUCCESS"
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert "submit.disabled" in js and "aria-busy" in js


# ── G. almacén de resultados ───────────────────────────────────────────────
def test_two_runs_for_the_same_client_and_am_get_distinct_opaque_ids_and_do_not_collide_with_the_catalog_cache(owner, fake_export):
    r1, r2 = _post(owner), _post(owner)
    a, b = _rid(r1), _rid(r2)
    assert a != b and re.fullmatch(r"[0-9a-f]{32}", a) and re.fullmatch(r"[0-9a-f]{32}", b)
    results = fake_export["cache"] / "adhoc" / "results"
    assert sorted(p.stem for p in results.glob("*.json")) == sorted([a, b])
    # espacio de claves del Catálogo: ni memoria ni sobres (cliente, AM) en la caché del Catálogo
    assert asvc._memory == {} and not list(fake_export["cache"].glob("*.envelope.json"))
    assert asvc._env_path("Acme", "4AM_ACME").parent == fake_export["cache"] and asvc._env_path("Acme", "4AM_ACME").parent != results


def test_results_live_24_hours_expired_ones_are_removed_and_a_process_replacement_clears_them(owner, fake_export, monkeypatch):
    rid = _rid(_post(owner))
    url = f"/modules/{STEM}/datasphere-assessment/result/{rid}"
    assert owner.get(url).status_code == 200 and owner.get(url + "/export.json").status_code == 200
    path = fake_export["cache"] / "adhoc" / "results" / f"{rid}.json"
    import time as _t
    _real_time = _t.time
    real_now = _real_time()
    monkeypatch.setattr(svc.time, "time", lambda: real_now + svc.RESULT_TTL_S + 5)
    gone = owner.get(url)
    assert gone.status_code == 404 and "venció" in gone.text and not path.exists()                  # vencido: 404 y se elimina
    monkeypatch.setattr(svc.time, "time", _real_time)
    rid2 = _rid(_post(owner))
    assert (fake_export["cache"] / "adhoc" / "results" / f"{rid2}.json").is_file()
    svc.reset_boot_state()                                                                          # "reemplazo de proceso/runtime"
    assert svc.load_result(rid2) is None and list((fake_export["cache"] / "adhoc" / "results").glob("*.json")) == []


def test_result_ids_are_validated_and_are_not_authorization(owner, fake_export, monkeypatch):
    rid = _rid(_post(owner))
    base = f"/modules/{STEM}/datasphere-assessment/result/"
    assert owner.get(base + "../../etc/passwd").status_code in (404, 422) and owner.get(base + "zzz").status_code == 404
    assert TestClient(main.app).get(base + rid).status_code == 401
    monkeypatch.setattr(access, "APP_OWNER", "other@danwor.com")
    assert owner.get(base + rid).status_code == 403 and owner.get(base + rid + "/export.json").status_code == 403
    assert owner.get(f"/modules/01_Arquitectura_y_Conectividad/datasphere-assessment/result/{rid}").status_code == 404


# ── H. informe reutilizado ─────────────────────────────────────────────────
def test_adhoc_report_is_the_same_template_and_presenter_without_lineage_or_catalog_context(owner, fake_export):
    r = _post(owner)
    rid = _rid(r)
    page = owner.get(f"/modules/{STEM}/datasphere-assessment/result/{rid}?pack=datasphere_core")
    assert page.status_code == 200
    t = page.text
    for needle in ("Indicadores del assessment", "Resumen", "Cobertura estricta", "Arquitectura &amp; Modelado", "Evidencia / Detalle técnico"):
        assert needle in t, needle
    assert "4AM_ACME" in t and "Assessment de Modelado &amp; Desarrollo · Acme" in t
    assert "Ver en linaje" not in t and "/catalog/flow" not in t and "Actualizar catálogo" not in t                  # sin linaje, sin flujo, sin Catálogo
    assert re.search(r'<a class="catalog-back" href="/modules/04_Modelado_y_Desarrollo\?pack=datasphere_core">← Volver</a>', t)
    assert f"/datasphere-assessment/result/{rid}/export.json" in t and f"/datasphere-assessment/result/{rid}/export.md" in t
    assert "Regenerar" not in t and "Generar informe" not in t                                                      # sin acciones de generación
    # misma plantilla: no existe una segunda plantilla de informe
    assert not list((REPO / "webapp/templates").rglob("*datasphere*report*")) and (REPO / "webapp/templates/partials/catalog_assessment_report.html").is_file()
    shell = (REPO / "webapp/templates/datasphere_assessment_result.html").read_text(encoding="utf-8")
    assert 'include "partials/catalog_assessment_report.html"' in shell
    # la misma vista con contexto de Catálogo SÍ ofrece linaje
    rec = svc.load_result(rid)
    class E:                                                                                            # entrada mínima del Catálogo
        entry_id, cliente, dominio, path, artefactos = "e1", "Acme", "Dom", "x", []
    cat = presenter.report_view(svc.result_status(rec), E(), "datasphere_core")
    assert any(o["lineage_url"] for g in cat["review_required"] + cat["observations"] for o in g["occurrences"]) and cat["adhoc"] is False
    ad = presenter.report_view(svc.result_status(rec), None, "datasphere_core", adhoc=presenter.AdhocReport("/b", "/j", "/m"))
    assert ad["adhoc"] is True and not any(o["lineage_url"] for g in ad["review_required"] + ad["observations"] for o in g["occurrences"])
    assert ad["report_url"] is None and ad["json_url"] == "/j" and ad["md_url"] == "/m" and ad["title_id"] == "4AM_ACME" and ad["title_desc"] is None


def test_adhoc_exports_come_from_the_stored_canonical_profile(owner, fake_export):
    from tools.catalog.modeling_assessment import profile as ma_profile
    from tools.catalog.modeling_assessment.render_md import render_markdown
    rid = _rid(_post(owner))
    rec = svc.load_result(rid)
    base = f"/modules/{STEM}/datasphere-assessment/result/{rid}"
    j, m = owner.get(base + "/export.json"), owner.get(base + "/export.md")
    assert j.status_code == m.status_code == 200
    assert j.text == ma_profile.canonical_assessment_json(rec["profile"]) and m.text == render_markdown(rec["profile"])
    assert 'filename="4AM_ACME.profile.json"' in j.headers["content-disposition"] and 'filename="4AM_ACME.profile.md"' in m.headers["content-disposition"]
    assert j.headers["content-type"].startswith("application/json") and m.headers["content-type"].startswith("text/markdown")
    calls = []
    asvc_engine = asvc._engine_assess
    import builtins                                                                                      # exportar NO regenera el assessment
    asvc._engine_assess = lambda *a, **k: calls.append(1) or asvc_engine(*a, **k)
    try:
        owner.get(base + "/export.json")
        owner.get(base)
    finally:
        asvc._engine_assess = asvc_engine
    assert calls == []


# ── I. desacople del loader del asistente y UX local ───────────────────────
def test_progress_panel_is_local_has_no_global_loader_and_no_actualizar_catalogo(owner):
    tpl = (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    run_form = tpl[tpl.index('<form id="dsa-form"'):tpl.index("</form>", tpl.index('<form id="dsa-form"'))]
    assert not re.search(r"section-analysis-form|assistant-chat-form|assistant-route-explain|assistant-request-loading|htmx-indicator|hx-indicator", run_form)
    assert "Obteniendo la mejor respuesta" not in tpl + js and not re.search(r"htmx|assistant|section-analysis|is-loading", js)
    # primitivo visual transversal reutilizado (solo markup/clases; sin CSS nuevo ni wiring del asistente)
    assert re.search(r'<div class="section-analysis-loading" role="status">\s*<span class="section-analysis-spinner" data-dsa-spinner hidden></span><span class="section-analysis-loading-copy" data-dsa-status', tpl)
    assert not re.search(r"assistant-request-loading|htmx-indicator|assistant-chat-loading", tpl)
    assert 'class="feedback-error" role="alert" data-dsa-failed' in tpl and "callout" not in tpl and "style=" not in tpl and "data-dsa-code" not in tpl+js
    assert not re.search(r"Esto cubre solo|Extracción completada|adquisición de la evidencia", tpl + js)
    assert "Obteniendo la mejor respuesta" not in tpl
    app_js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert "data-dsa" not in app_js and "#dsa-" not in app_js and "datasphere_assessment" not in app_js
    assert 'data-running-text="Ejecutando extracción..."' in tpl and "data-dsa-progress" in tpl and "data-dsa-log" in tpl
    assert not re.search(r"Actualizar cat|refresh-catalog|data-dsa-refresh|Extracción completada|solo la <strong>adquisición", tpl + js)   # ya no se expone
    assert "window.location.assign(done.result_url)" in js and "event.v" in js and "textContent" in js     # navega solo al informe; fases/logs como texto
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", js)
    assert not re.search(r"\d+\s*%|<progress|role=\"progressbar\"", tpl + js)
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core").text
    panel = html[html.index('id="dsa-panel"'):html.index('id="dsa-regen"')] if 'id="dsa-regen"' in html else html[html.index('id="dsa-panel"'):]
    assert "assistant-request-loading" not in panel[:panel.index("</form>")] and "Obteniendo la mejor respuesta" not in panel[:panel.index("</form>")]


def test_stdout_with_html_like_text_travels_as_data(owner, fake_export):
    fake_export["extra_lines"] = ["<script>alert(1)</script> <img src=x onerror=alert(2)>"]
    r = _post(owner)
    assert "<script>alert(1)</script> <img src=x onerror=alert(2)>" in _lines(r) and r.headers["content-type"].startswith("application/x-ndjson")
    assert "|safe" not in (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("message,code", [
    ("PRECHECK_OAUTH_401 - OAuth rechazó las credenciales (HTTP 401).", "AUTHENTICATION_FAILED"),
    ("PRECHECK_OAUTH_NETWORK_ERROR - No se pudo contactar el endpoint OAuth.", "OAUTH_ENDPOINT_UNREACHABLE"),
    ("PRECHECK_AUTH_ERROR - No se pudo validar acceso al Space.", "SPACE_ACCESS_FAILED"),
    ("boom", "EXTRACTION_FAILED")])
def test_backend_messages_are_preserved_and_failures_are_normalized(owner, fake_export, monkeypatch, message, code):
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError(message)))
    r = _post(owner)
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == code and fake_export["exports"] == 0
    assert d["message"] == (message if message.startswith("PRECHECK_") else svc.ERROR_MESSAGES[code])
    assert any(message in line for line in _lines(r)) and "Traceback" not in r.text and SECRET not in r.text
    assert not re.search(r"PRECHECK_|401|403", (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8"))


def test_cli_unavailable_has_its_own_code_and_creates_no_workspace(owner, fake_export, monkeypatch):
    monkeypatch.setattr(svc, "cli_available", lambda: False)
    assert _done(_post(owner))["error_code"] == "DATASPHERE_CLI_UNAVAILABLE"
    assert not (fake_export["cache"] / "adhoc" / "work").exists() or list((fake_export["cache"] / "adhoc" / "work").iterdir()) == []


# ── J. el Catálogo no cambia ───────────────────────────────────────────────
def test_catalog_regeneration_capability_is_unchanged_but_no_longer_part_of_the_adhoc_flow(owner, fake_export, monkeypatch):
    seen = {}
    monkeypatch.setattr(dsp, "regenerate_catalog_from_pool", lambda csn, cat: seen.update(csn=csn, cat=cat) or "resumen")
    cleared = []
    monkeypatch.setattr(main, "_invalidate_catalog_cache", lambda: cleared.append(1))
    r = owner.post(REFRESH, data={"pack": "datasphere_core"}, headers={**ORIGIN, **HX})
    assert r.status_code == 200 and "Catálogo actualizado" in r.text and cleared == [1] and seen["csn"].name == ".csn_cache"
    assert owner.post(REFRESH, data={"pack": "datasphere_core"}, headers=HX).status_code == 403
    assert "refresh-catalog" not in (REPO / "webapp/templates/partials/datasphere_assessment_tool.html").read_text(encoding="utf-8")


def test_catalog_assessment_cache_namespace_is_not_reused_by_adhoc_code():
    src = (REPO / "webapp/services/datasphere_assessment_service.py").read_text(encoding="utf-8")
    for forbidden in ("_store_envelope", "_load_envelope", "_memory", "_env_path", "generate("):
        assert forbidden not in src, forbidden
    assert re.search(r"\badhoc\b", src) and "assess_evidence_root" in src


def test_modeling_assessment_package_is_only_reached_through_the_assessment_service(owner):
    for rel in ("webapp/services/datasphere_assessment_service.py", "webapp/routers/datasphere_assessment.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        assert "modeling_assessment" not in src, rel


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


def test_tool_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no disponible")
    import subprocess
    r = subprocess.run([node, "--check", str(REPO / "webapp/static/datasphere_assessment.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
