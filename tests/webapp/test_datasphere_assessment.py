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
         "datasphere_root_object": "4AM_ACME", "client": "Acme"}


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
    calls = {"exports": 0, "cli": [], "extra_lines": [], "requests": [], "out_dirs": [], "effective": []}
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
        self.root_type = calls.get("root_type", "analytic-model")                           # lo deriva el extractor (no la web)
        self.normalize_scope()                                                             # dependencias REALES del extractor (requested -> effective)
        calls["effective"].append(set(self.effective_features))
        self.validate_root_constraint()                                                    # compuerta REAL del extractor (antes de la evidencia profunda)
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
    for needle in ('type="password"', 'name="datasphere_client_secret"', 'autocomplete="off"', 'autocapitalize="none"', 'spellcheck="false"'):
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
                     ("space", "text"), ("datasphere_root_object", "text"), ("client", "text")]
    labels = re.findall(r'<label class="tool-label" for="dsa-([a-z_]+)">([^<]+)</label>', form)
    assert [t for _n, t in labels] == ["URL del tenant", "Token URL (OAuth)", "Client ID", "Client Secret", "Space ID", "Artefacto / Objeto", "Nombre de Cliente"]
    outside_scope = re.sub(r"<details class=\"admin-tool catalog-picker--secondary\" data-dsa-scope>.*?</details>", "", form, flags=re.S)
    assert "admin-help" not in outside_scope and "style=" not in form      # sin helpers en el formulario salvo la explicación DENTRO del alcance de evidencia
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
        vals = svc.FormValues(host=url.rsplit("/oauth", 1)[0], token_url=url, client_id="id", space="S", root_object="4AM_ACME", client="Acme")
        out = svc.run(vals, SECRET)
        assert out.state == "FAILED" and out.error_code == "AUTHENTICATION_FAILED" and out.stage == "extraction" and runs == []
        assert out.message.startswith("OAuth rechazó las credenciales") and "PRECHECK_" not in out.message and SECRET not in " ".join(out.trace)
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
    monkeypatch.setattr(ma_shadow, "assess", lambda inbound, client, am, domain=None, **kw: (seen.append((Path(inbound), client, am, domain)), real(inbound, client, am, domain, **kw))[1])
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
    vals = svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", root_object="4AM_ACME", client="Acme")
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
    assert d["message"].startswith("OAuth válido, pero sin permisos") and "PRECHECK_" not in d["message"] and d["result_url"] is None and _phases(r) == []
    assert not list((fake_export["cache"] / "adhoc" / "results").glob("*.json"))


def test_assessment_failure_is_not_labeled_as_extraction_and_keeps_the_log(owner, fake_export, monkeypatch):
    monkeypatch.setattr(asvc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"boom {SECRET}")))
    r = _post(owner)
    d = _done(r)
    assert d["state"] == "FAILED" and d["title"] == "No se pudo generar el assessment" and d["error_code"] == "GENERATION_ERROR"
    assert d["message"].startswith("La evidencia se adquirió correctamente") and "boom" not in d["message"] and SECRET not in r.text
    assert _phases(r) == ["Evidencia adquirida", "Generando assessment..."] and _lines(r)                   # el detalle técnico sigue disponible
    assert d["result_url"] is None and not list((fake_export["cache"] / "adhoc" / "results").glob("*.json"))


def test_object_missing_from_the_evidence_is_an_assessment_stage_failure_with_its_own_code(owner, fake_export):
    r = _post(owner, dict(VALID, datasphere_root_object="NO_EXISTE_AM"))
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == "ROOT_NOT_IN_EVIDENCE" and d["title"] == "No se pudo generar el assessment"
    assert d["message"] == "La evidencia se adquirió correctamente, pero el objeto indicado no figura en ella. Revisá el objeto seleccionado."
    assert "AM_NOT_TERMINAL" not in d["message"] and d["error_code"] != "AM_NOT_TERMINAL"           # un objeto que no es AM ya NO es un "AM no terminal"


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
    vals = svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", root_object="4AM_ACME", client="Acme")
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
    # espacio de claves del Catálogo: ni memoria ni sobres (cliente, AM) en el almacén durable del assessment GOBERNADO (el ad-hoc es efímero)
    assert asvc._memory == {} and not list(fake_export["cache"].glob("*.envelope.json"))
    gov = asvc.governed_path("Acme", "4AM_ACME")
    assert not gov.exists() and not list(asvc.governed_dir().rglob("*.json"))
    assert asvc.governed_dir().resolve() not in results.resolve().parents


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
    for needle in ("Indicadores del assessment", "Resumen", "Cobertura estricta", "Arquitectura &amp; Modelado", "Cierre: limitaciones y trazabilidad"):
        assert needle in t, needle
    assert "4AM_ACME" in t and "Assessment de Modelado &amp; Desarrollo · Acme" in t
    assert "Ver en linaje" not in t and "/catalog/flow" not in t and "Actualizar catálogo" not in t                  # sin linaje, sin flujo, sin Catálogo
    assert re.search(r'<a class="catalog-back" href="/modules/04_Modelado_y_Desarrollo\?pack=datasphere_core&amp;tool=assessment">← Volver</a>', t)
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
    assert d["message"] == (message.split(" - ", 1)[1] if message.startswith("PRECHECK_") else svc.ERROR_MESSAGES[code])
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


# ── contrato root_object ───────────────────────────────────────────────────
def test_form_uses_root_object_contract_and_no_legacy_analytic_model_field(owner):
    form = _form_html(owner)
    assert 'name="datasphere_root_object"' in form and 'id="dsa-root_object"' in form and ">Artefacto / Objeto</label>" in form
    assert not re.search(r"analytic_model|Modelo Anal|Analytic Model|root_type|name=\"mode\"|name=\"features|datasphere_analytic", form)
    assert "analytic_model" not in (REPO / "webapp/services/datasphere_assessment_service.py").read_text(encoding="utf-8")
    assert "analytic_model" not in (REPO / "webapp/routers/datasphere_assessment.py").read_text(encoding="utf-8")


def test_service_passes_root_object_and_forces_assessment_mode_server_side(owner, fake_export):
    r = _post(owner, dict(VALID, mode="publish", root_type="view", features="views", analytic_model="OTRO", generate_catalog="1"))   # intento de manipulación
    assert _done(r)["state"] == "SUCCESS"
    req = fake_export["requests"][0]
    assert req.root_object == "4AM_ACME" and not req.analytic_model                      # sin traducción root_object -> analytic_model
    assert req.mode == "assessment" and req.generate_catalog is False and req.mirror_up is False
    assert req.root_type is None and not req.features_requested                          # root_type lo deriva el extractor; features fuera de este patch


def test_root_object_validation_copy_is_generic(owner, fake_export):
    d = _done(_post(owner, dict(VALID, datasphere_root_object="a b!")))
    assert d["state"] == "INVALID" and d["errors"]["root_object"] == "Ingresá un artefacto u objeto válido (letras, números, _ . -)."
    assert not re.search(r"Analytic Model|terminal", json.dumps(d["errors"]))
    assert fake_export["exports"] == 0


def test_extractor_failure_for_missing_root_keeps_backend_wording(owner, fake_export, monkeypatch):
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError("ROOT_OBJECT_NOT_FOUND - No se encontró 'X' en el Space 'S'.")))
    d = _done(_post(owner))
    assert d["state"] == "FAILED" and d["error_code"] == "ROOT_OBJECT_NOT_FOUND" and d["title"] == "No se pudo completar la extracción"
    assert d["message"].startswith("No se encontró 'X'") and "ROOT_OBJECT_NOT_FOUND" not in d["message"]   # texto del extractor; el código va al detalle


@pytest.mark.parametrize("raised,code", [
    ("PRECHECK_OAUTH_401 - OAuth rechazó las credenciales (HTTP 401). Revisá Client ID.", "AUTHENTICATION_FAILED"),
    ("PRECHECK_AUTH_403 - OAuth válido, pero sin permisos.", "INSUFFICIENT_DATASPHERE_PRIVILEGES"),
    ("ROOT_OBJECT_NOT_FOUND - No se encontró 'X' en el Space 'S'.", "ROOT_OBJECT_NOT_FOUND"),
    ("AMBIGUOUS_ROOT_OBJECT_TYPE - El technical name 'X' existe en múltiples tipos: view, local-table.", "AMBIGUOUS_ROOT_OBJECT_TYPE")])
def test_visible_error_has_no_technical_identifiers_which_live_only_in_technical_detail(owner, fake_export, monkeypatch, raised, code):
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError(raised)))
    r = _post(owner)
    d = _done(r)
    visible = d["title"] + " " + d["message"]
    assert d["state"] == "FAILED" and d["error_code"] == code and d["title"] == "No se pudo completar la extracción"
    assert not re.search(r"PRECHECK_|AUTHENTICATION_FAILED|INSUFFICIENT_|ROOT_OBJECT_NOT_FOUND|AMBIGUOUS_ROOT_OBJECT_TYPE|AM_NOT_TERMINAL", visible)
    assert any(raised.split(" - ")[0] in line for line in _lines(r))                      # el identificador sí está en el detalle técnico (stdout)


def test_assessment_failure_visible_copy_has_no_engine_code_but_code_is_in_detail_channel(owner, fake_export):
    d = _done(_post(owner, dict(VALID, datasphere_root_object="NO_EXISTE_AM")))
    assert d["error_code"] == "ROOT_NOT_IN_EVIDENCE" and "ROOT_NOT_IN_EVIDENCE" not in d["title"] + d["message"]
    assert "done.error_code" in (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")   # JS lo agrega a "Detalle técnico"


# ── raíz genérica: el assessment ya NO restringe el tipo de objeto raíz ──────
def _form_html_for(c):
    html = c.get(f"/modules/{STEM}?pack=datasphere_core").text
    start = html.index('<form id="dsa-form"')
    return html[start:html.index("</form>", start)]


def test_server_side_request_no_longer_restricts_root_types(owner, fake_export):
    assert _done(_post(owner))["state"] == "SUCCESS"
    req = fake_export["requests"][0]
    assert req.allowed_root_types is None and req.mode == "assessment" and req.root_object == "4AM_ACME"      # sin compuerta de tipo


def test_browser_cannot_inject_root_policy_or_publication_controls(owner, fake_export):
    r = _post(owner, dict(VALID, allowed_root_types="view,local-table,remote-table,analytic-model", root_type="view", mode="publish",
                          analytic_model="OTRO", features="views", generate_catalog="1", datasphere_allowed_root_types="view"))
    assert _done(r)["state"] == "SUCCESS"
    req = fake_export["requests"][0]
    assert req.allowed_root_types is None and req.root_type is None and req.mode == "assessment"                 # el navegador no inyecta tipo ni restricciones
    assert not req.analytic_model and not req.features_requested and req.generate_catalog is False
    assert not re.search(r"allowed_root_types|root_type|name=.mode.|name=.features", _form_html_for(owner))


@pytest.mark.parametrize("root,root_type", [("3VF_P", "view"), ("1VR_RAW", "view"), ("RAW_T", "remote-table")])
def test_non_am_roots_reach_a_valid_assessment_and_report_instead_of_a_root_gate_failure(owner, fake_export, root, root_type):
    fake_export["root_type"] = root_type
    r = _post(owner, dict(VALID, datasphere_root_object=root))
    d = _done(r)
    assert d["state"] == "SUCCESS" and d["result_url"], d
    assert d["error_code"] is None and "ROOT_TYPE_CONSTRAINT_FAILED" not in r.text and "AM_NOT_TERMINAL" not in r.text
    rec = svc.load_result(_rid(r))
    scope = rec["profile"]["scope"]
    assert scope["root_object"] == root and scope["root_type"] == root_type and scope["terminal_analytic_model"] is None      # sin AM fabricado
    page = _report_html(owner, _rid(r))
    assert page.count(root) >= 1 and "Modelo analítico terminal:" not in page


def test_analytic_model_path_is_unchanged_and_other_errors_keep_their_presentation(owner, fake_export, monkeypatch):
    d = _done(_post(owner))
    assert d["state"] == "SUCCESS" and d["result_url"]
    d2 = _done(_post(owner, dict(VALID, datasphere_root_object="NO_EXISTE_AM")))
    assert d2["error_code"] == "ROOT_NOT_IN_EVIDENCE" and d2["title"] == "No se pudo generar el assessment" and "evidencia se adquirió" in d2["message"]
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight",
                        lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError("ROOT_OBJECT_NOT_FOUND - No se encontró 'X' en el Space 'S'.")))
    d3 = _done(_post(owner))
    assert d3["error_code"] == "ROOT_OBJECT_NOT_FOUND" and d3["title"] == "No se pudo completar la extracción"
    assert "ROOT_OBJECT_NOT_FOUND" not in d3["message"]


# ── continuidad: contexto de formulario, assessments recientes, log de ejecución ──
def _page(c):
    return c.get(f"/modules/{STEM}?pack=datasphere_core").text


def _input(html, ident):
    return re.search(rf'<input id="{ident}"[^>]*>', html).group(0)


def _results_url(rid, suffix=""):
    return f"/modules/{STEM}/datasphere-assessment/result/{rid}{suffix}"


@pytest.fixture()
def two_owners(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com,second@danwor.com")
    svc._RUNNING.clear()
    return _client("owner@danwor.com"), _client("second@danwor.com")


def test_form_retains_non_sensitive_values_but_never_the_client_secret(owner, fake_export):
    import base64 as _b64
    assert "value=" not in _input(_page(owner), "dsa-host")                              # primera vez: vacío
    assert _done(_post(owner))["state"] == "SUCCESS"
    html = _page(owner)                                                                  # "Volver": la página del módulo se vuelve a pedir
    for ident, value in (("dsa-host", VALID["host"]), ("dsa-token_url", VALID["token_url"]), ("dsa-client_id", VALID["datasphere_client_id"]),
                         ("dsa-space", VALID["space"]), ("dsa-root_object", VALID["datasphere_root_object"]), ("dsa-client", VALID["client"])):
        assert f'value="{value}"' in _input(html, ident), ident
    sec = _input(html, "dsa-client_secret")
    assert "value=" not in sec and 'type="password"' in sec and SECRET not in html
    stored = "".join(p.read_text(encoding="utf-8") for p in (fake_export["cache"] / "adhoc").rglob("*") if p.is_file())
    assert SECRET not in stored and _b64.b64encode(SECRET.encode()).decode() not in stored   # el secreto no está en NINGÚN almacén ad-hoc


def test_form_context_is_session_scoped_ttl_bound_and_ignores_client_supplied_state(owner, fake_export, monkeypatch):
    import time as _t
    real = _t.time
    _post(owner)
    assert 'value="ACME_SPACE"' in _input(_page(owner), "dsa-space")
    other_session = _client("owner@danwor.com")
    other_session.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "owner@danwor.com", "name": "T2", "first_name": "T", "family_name": "U"}))
    assert "value=" not in _input(_page(other_session), "dsa-space")                     # otro login => otra cookie => contexto vacío
    forged = owner.get(f"/modules/{STEM}?pack=datasphere_core&datasphere_client_secret={SECRET}&dsa-client_secret={SECRET}").text
    assert SECRET not in forged and "value=" not in _input(forged, "dsa-client_secret")  # estado del cliente no recupera ni inyecta un secreto
    monkeypatch.setattr(svc.time, "time", lambda: real() + svc.RESULT_TTL_S + 5)
    assert "value=" not in _input(_page(owner), "dsa-space")                             # el contexto vence con el TTL


def test_context_is_saved_even_when_the_run_fails_but_still_without_the_secret(owner, fake_export, monkeypatch):
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: (_ for _ in ()).throw(dsp.DatasphereCliError("PRECHECK_AUTH_403 - x")))
    assert _done(_post(owner))["state"] == "FAILED"
    html = _page(owner)
    assert 'value="Acme"' in _input(html, "dsa-client") and SECRET not in html


def test_recent_section_absent_without_results_and_present_with_live_ones_ordered_newest_first(owner, fake_export, monkeypatch):
    import time as _t
    real = _t.time
    assert "Assessments recientes" not in _page(owner) and "Historial" not in _page(owner)
    clock = {"d": 0}
    monkeypatch.setattr(svc.time, "time", lambda: real() + clock["d"])
    first = _rid(_post(owner))
    clock["d"] = 60
    second = _rid(_post(owner, dict(VALID, client="Acme2")))
    html = _page(owner)
    assert "Assessments recientes" in html and "Historial" not in html
    assert html.index(second) < html.index(first)                                        # más reciente primero
    assert html.count("Abrir assessment") == 2 and html.count("Ver ejecución") == 2
    assert f'href="{_results_url(first)}?pack=datasphere_core"' in html and str(fake_export["cache"]) not in html   # sin rutas del sistema
    assert owner.get(_results_url(second) + "?pack=datasphere_core").status_code == 200   # abrir = el informe existente


def test_expired_and_runtime_lost_results_disappear_from_the_list_and_fail_safely(owner, fake_export, monkeypatch):
    import time as _t
    real = _t.time
    rid = _rid(_post(owner))
    assert "Assessments recientes" in _page(owner)
    monkeypatch.setattr(svc.time, "time", lambda: real() + svc.RESULT_TTL_S + 5)
    assert "Assessments recientes" not in _page(owner)                                   # todos vencidos => desaparece la sección entera
    assert owner.get(_results_url(rid)).status_code == 404 and owner.get(_results_url(rid, "/log")).status_code == 404
    monkeypatch.setattr(svc.time, "time", real)
    rid2 = _rid(_post(owner))
    svc.reset_boot_state()                                                               # reemplazo de proceso/runtime
    assert "Assessments recientes" not in _page(owner)
    assert owner.get(_results_url(rid2)).status_code == 404


def test_recent_results_and_their_log_are_isolated_per_authenticated_owner(two_owners, fake_export):
    first, second = two_owners
    rid = _rid(_post(first))
    assert "Assessments recientes" in _page(first) and "Assessments recientes" not in _page(second)
    assert rid not in _page(second)
    for suffix in ("", "/export.json", "/export.md", "/log"):                            # el result_id no autoriza: indistinguible de inexistente
        assert second.get(_results_url(rid, suffix)).status_code == 404
        assert first.get(_results_url(rid, suffix)).status_code == 200
    assert TestClient(main.app).get(_results_url(rid, "/log")).status_code == 401


def test_execution_log_reopens_with_the_result_and_has_no_credentials(owner, fake_export):
    import base64 as _b64
    import urllib.parse as _up
    basic = _b64.b64encode(f"{VALID['datasphere_client_id']}:{SECRET}".encode()).decode()
    fake_export["extra_lines"] = [f"eco {SECRET}", f"Authorization: Basic {basic}", f"id:secret en base64 {basic}", f"urlenc {_up.quote(SECRET, safe='')}",
                                  "Authorization: Bearer abcdefghijklmnop", '{"access_token": "tok-123456789"}', "linea normal del extractor"]
    rid = _rid(_post(owner))
    page = owner.get(_results_url(rid, "/log") + "?pack=datasphere_core")
    assert page.status_code == 200 and "Detalle de ejecución" in page.text and "Detalle técnico" in page.text and "linea normal del extractor" in page.text
    assert 'class="admin-tool"' in page.text and "admin-result-output" in page.text         # reusa el primitivo existente
    stored = svc.load_result(rid)
    blob = page.text + json.dumps(stored["execution_log"])
    for forbidden in (SECRET, basic, _up.quote(SECRET, safe=""), "abcdefghijklmnop", "tok-123456789"):
        assert forbidden not in blob, forbidden
    assert stored["execution_log"] and "[REDACTED]" in " ".join(stored["execution_log"])


def test_execution_log_is_not_part_of_the_canonical_exports_and_exports_are_unchanged(owner, fake_export):
    from tools.catalog.modeling_assessment import profile as ma_profile
    from tools.catalog.modeling_assessment.render_md import render_markdown
    rid = _rid(_post(owner))
    rec = svc.load_result(rid)
    j, m = owner.get(_results_url(rid, "/export.json")), owner.get(_results_url(rid, "/export.md"))
    assert j.text == ma_profile.canonical_assessment_json(rec["profile"]) and m.text == render_markdown(rec["profile"])
    assert "Preflight OAuth" not in j.text + m.text and "execution_log" not in j.text


def test_log_disappears_with_the_result(owner, fake_export):
    rid = _rid(_post(owner))
    assert owner.get(_results_url(rid, "/log")).status_code == 200
    (fake_export["cache"] / "adhoc" / "results" / f"{rid}.json").unlink()
    assert owner.get(_results_url(rid, "/log")).status_code == 404 and "Assessments recientes" not in _page(owner)


def test_password_field_contract_type_password_autocomplete_off_no_server_value(owner, fake_export):
    _post(owner)
    sec = _input(_page(owner), "dsa-client_secret")
    assert 'type="password"' in sec and 'autocomplete="off"' in sec and "new-password" not in sec and "value=" not in sec
    assert 'autocapitalize="none"' in sec and 'spellcheck="false"' in sec


# ── nombre de negocio del AM en el assessment ad-hoc (evidencia -> perfil canónico -> JSON/MD/HTML) ──
def _label_the_evidence(fake_export, label):
    path = fake_export["evidence"] / "Acme" / "analytic-models" / "4AM_ACME.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["definitions"]["4AM_ACME"]["@EndUserText.label"] = label
    path.write_text(json.dumps(data), encoding="utf-8")


def test_adhoc_assessment_carries_the_business_name_into_json_markdown_and_html(owner, fake_export):
    label = "Modelo analítico trazabilidad de Pagos - Factura - Compra"
    _label_the_evidence(fake_export, label)
    rid = _rid(_post(owner))
    rec = svc.load_result(rid)
    assert rec["profile"]["scope"]["terminal_analytic_model_label"] == label
    j = owner.get(_results_url(rid, "/export.json")).text
    m = owner.get(_results_url(rid, "/export.md")).text
    page = owner.get(_results_url(rid) + "?pack=datasphere_core").text
    assert f'"terminal_analytic_model_label": "{label}"' in j and f"- **Descripción:** {label}" in m and "- **Modelo analítico terminal:** 4AM_ACME" in m
    assert label in page and "4AM_ACME" in page and page.index(label) < page.index("4AM_ACME", page.index(label) - 200)
    assert "None" not in page.split("<main")[1].split("Indicadores")[0]


def test_adhoc_assessment_without_a_business_label_keeps_the_technical_title_only(owner, fake_export):
    rid = _rid(_post(owner))
    assert svc.load_result(rid)["profile"]["scope"]["terminal_analytic_model_label"] is None
    page = owner.get(_results_url(rid) + "?pack=datasphere_core").text
    assert "Descripción" not in owner.get(_results_url(rid, "/export.md")).text
    head = page.split("<main")[1].split("Indicadores")[0]
    assert "4AM_ACME" in head and "None" not in head and "—" not in head


# ── alcance de evidencia (familias opcionales) ─────────────────────────────────
CANON_ALL = ["analytic-models", "views", "tables", "replication-flows", "data-flows", "transformation-flows", "task-chains"]


def _scope_form(owner):
    return re.search(r'<details class="admin-tool catalog-picker--secondary" data-dsa-scope>.*?</details>', _form_html_for(owner), re.S).group(0)


def test_scope_disclosure_is_collapsed_by_default_directly_below_the_object_field_with_default_all_selected(owner):
    form = _form_html_for(owner)
    block = _scope_form(owner)
    assert " open" not in block.split(">", 1)[0] and ">Alcance de evidencia</summary>" in block
    assert form.index('id="dsa-root_object"') < form.index("data-dsa-scope") < form.index('id="dsa-client"')      # justo debajo de Artefacto / Objeto
    boxes = re.findall(r'<input type="checkbox" name="datasphere_features" value="([^"]+)"( checked)?>', block)
    assert [(v, bool(c)) for v, c in boxes] == [("replication-flows", True), ("data-flows", True), ("transformation-flows", True), ("task-chains", True)]
    assert re.findall(r"<span>([^<]+)</span></label>", block) == ["Replication Flows", "Data Flows", "Transformation Flows", "Task Chains"]
    assert "Seleccioná qué tipos de evidencia incluir en la extracción." in block and "limita el alcance de evidencia" in block
    assert 'name="datasphere_features_submitted" value="1"' in block
    assert "dashboard-checkbox-group" in block and "dashboard-checkbox-option" in block and "style=" not in block      # primitivos del Hub, sin CSS propio
    assert not re.search(r"analytic-models|name=.views.|value=.tables.|datasphere_mode|root_type|allowed_root_types|name=.mode.", form)   # estructurales NO elegibles


def test_not_submitted_features_keep_todays_default_all_extraction(owner, fake_export):
    _post(owner)                                                                         # sin campos de alcance (cliente viejo / POST directo)
    req = fake_export["requests"][0]
    assert req.features_requested is None                                                # default del extractor = todas
    assert fake_export["effective"][0] == set(CANON_ALL)


def test_all_selected_is_equivalent_to_the_default_extraction(owner, fake_export):
    _post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows", "data-flows", "transformation-flows", "task-chains"]})
    assert set(fake_export["requests"][0].features_requested) == set(CANON_ALL) and fake_export["effective"][0] == set(CANON_ALL)


def test_selected_features_reach_the_public_facade_as_features_requested(owner, fake_export):
    r = _post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]})
    assert _done(r)["state"] == "SUCCESS"
    req = fake_export["requests"][0]
    assert req.features_requested == ["analytic-models", "views", "tables", "replication-flows"]      # estructurales siempre + lo elegido
    assert not hasattr(req, "requested_features")                                        # la web habla con la fachada pública, no con LineageExporter
    rec = svc.load_result(_rid(r))
    es = rec["profile"]["scope"]["evidence_scope"]
    assert es == {"requested_features": sorted(req.features_requested), "effective_features": sorted(fake_export["effective"][0])}
    assert "Evidencia no adquirida" in owner.get(_results_url(_rid(r), "/export.md")).text


def test_unchecking_everything_requests_only_the_structural_families(owner, fake_export):
    assert _done(_post(owner, {**VALID, "datasphere_features_submitted": "1"}))["state"] == "SUCCESS"
    assert fake_export["requests"][0].features_requested == ["analytic-models", "views", "tables"]


def test_manipulated_features_are_whitelisted_and_server_policy_is_untouched(owner, fake_export):
    r = _post(owner, {**VALID, "datasphere_features_submitted": "1",
                      "datasphere_features": ["data-flows", "evil-family", "views", "tables", "../etc", "analytic-models,task-chains"],
                      "mode": "publish", "root_type": "view", "allowed_root_types": "view", "generate_catalog": "1", "mirror_up": "1"})
    assert _done(r)["state"] == "SUCCESS"
    req = fake_export["requests"][0]
    assert req.features_requested == ["analytic-models", "views", "tables", "data-flows"]          # solo la lista blanca canónica
    assert req.allowed_root_types is None and req.mode == "assessment"
    assert req.generate_catalog is False and req.mirror_up is False and req.root_type is None


def test_task_chains_without_any_flow_family_gets_the_extractors_backend_validation(owner, fake_export):
    r = _post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["task-chains"]})
    d = _done(r)
    assert d["state"] == "FAILED" and d["error_code"] == "INVALID_FEATURE_SCOPE" and d["title"] == "No se pudo completar la extracción"
    assert d["message"].startswith("Task Chains requiere al menos una familia de flows") and "INVALID_FEATURE_SCOPE" not in d["message"]
    assert any("INVALID_FEATURE_SCOPE" in line for line in _lines(r)) and d["result_url"] is None


def test_scope_selection_survives_report_and_back_but_client_secret_does_not(owner, fake_export):
    _post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows", "task-chains"]})
    html = _page(owner)
    block = re.search(r'data-dsa-scope>.*?</details>', html, re.S).group(0)
    checked = dict(re.findall(r'name="datasphere_features" value="([^"]+)"( checked)?>', block))
    assert {k: bool(v) for k, v in checked.items()} == {"replication-flows": True, "data-flows": False, "transformation-flows": False, "task-chains": True}
    assert "value=" not in _input(html, "dsa-client_secret") and SECRET not in html
    stored = "".join(p.read_text(encoding="utf-8") for p in (fake_export["cache"] / "adhoc").rglob("*") if p.is_file())
    assert SECRET not in stored


def test_unsubmitted_form_context_defaults_to_all_checked_again_after_a_run_without_scope(owner, fake_export):
    _post(owner)                                                                         # sin alcance enviado => el contexto no guarda alcance
    block = re.search(r'data-dsa-scope>.*?</details>', _page(owner), re.S).group(0)
    assert block.count(" checked>") == 4


def test_historical_results_keep_their_own_scope_when_the_form_changes(owner, fake_export):
    first = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]}))
    second = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows", "data-flows", "transformation-flows", "task-chains"]}))
    s1 = svc.load_result(first)["profile"]["scope"]["evidence_scope"]["effective_features"]
    s2 = svc.load_result(second)["profile"]["scope"]["evidence_scope"]["effective_features"]
    assert "data-flows" not in s1 and "data-flows" in s2                                   # cada resultado conserva el alcance de SU corrida
    assert "Evidencia no adquirida" in owner.get(_results_url(first, "/export.md")).text
    assert "Evidencia no adquirida" not in owner.get(_results_url(second, "/export.md")).text


def test_view_root_with_a_feature_selection_still_generates_the_assessment_with_its_scope(owner, fake_export):
    fake_export["root_type"] = "view"
    r = _post(owner, {**VALID, "datasphere_root_object": "3VF_P", "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]})
    assert _done(r)["state"] == "SUCCESS"
    es = svc.load_result(_rid(r))["profile"]["scope"]["evidence_scope"]
    assert "replication-flows" in es["effective_features"] and "data-flows" not in es["effective_features"]


def test_log_keeps_the_extractors_effective_scope_when_it_prints_it(owner, fake_export):
    fake_export["extra_lines"] = ["Alcance efectivo:", "  Replication Flows        SI"]
    r = _post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]})
    page = owner.get(_results_url(_rid(r), "/log") + "?pack=datasphere_core").text
    assert "Alcance efectivo:" in page and "Replication Flows        SI" in page


# ── eliminar un assessment reciente ────────────────────────────────────────────
def _delete(c, rid, accept="application/json", **extra):
    return c.post(_results_url(rid, "/delete"), data={"pack": "datasphere_core"}, headers={**ORIGIN, "Accept": accept, **extra})


def test_recent_item_renders_the_capital_humano_delete_pattern_with_accessible_label(owner, fake_export):
    _post(owner)
    html = _page(owner)
    item = re.search(r"<div class=\"catalog-picker--secondary\" data-dsa-recent-item>.*?</form>", html, re.S).group(0)
    # MISMO patrón/SVG que Capital Humano (el panel de Entrevistas, partials/human_capital_entrevistas_panel.html — antes inline en human_capital/entrevistas.html): form de confirmación compartida + botón de acción destructiva
    ch = (REPO / "webapp/templates/partials/human_capital_entrevistas_panel.html").read_text(encoding="utf-8")
    ch_svg = re.search(r'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18"/>.*?</svg>', ch, re.S).group(0)
    assert ch_svg in item
    assert 'class="table-action-btn table-action-btn--danger chart-tooltip-anchor" aria-label="Eliminar assessment"' in item
    assert "<p>Eliminar assessment</p>" in item and 'data-dw-confirm-form' in item
    assert 'data-dw-confirm-title="¿Eliminar este assessment?"' in item
    assert "Este resultado temporal y su detalle de ejecución dejarán de estar disponibles." in item and 'data-dw-confirm-ok="Eliminar"' in item
    assert 'method="post"' in item and re.search(r'action="/modules/[^"]+/datasphere-assessment/result/[0-9a-f]{32}/delete"', item)
    assert "style=" not in item and "🗑" not in item and ">X<" not in item                                   # sin emoji ni texto "X" ni estilos propios
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert css.count(".admin-actions__end") == 1                                                             # única extensión: alinear a la derecha


def test_owner_deletes_own_result_and_it_cannot_be_opened_nor_its_log(owner, fake_export):
    rid = _rid(_post(owner))
    r = _delete(owner, rid)
    assert r.status_code == 200 and r.json() == {"ok": True, "remaining": 0}
    for suffix in ("", "/export.json", "/export.md", "/log"):
        assert owner.get(_results_url(rid, suffix)).status_code == 404
    assert not (fake_export["cache"] / "adhoc" / "results" / f"{rid}.json").exists()
    assert "Assessments recientes" not in _page(owner)                                                        # último resultado => sección oculta


def test_deleting_one_leaves_the_others_and_the_form_context_intact(owner, fake_export):
    first = _rid(_post(owner))
    second = _rid(_post(owner, dict(VALID, client="Acme2")))
    assert _delete(owner, first).json() == {"ok": True, "remaining": 1}
    html = _page(owner)
    assert "Assessments recientes" in html and second in html and first not in html
    assert 'value="Acme2"' in _input(html, "dsa-client") and 'value="ACME_SPACE"' in _input(html, "dsa-space")        # contexto del formulario intacto
    assert "value=" not in _input(html, "dsa-client_secret") and SECRET not in html
    assert owner.get(_results_url(second)).status_code == 200


def test_another_owner_cannot_delete_a_result_by_guessing_its_id(two_owners, fake_export):
    first, second = two_owners
    rid = _rid(_post(first))
    assert _delete(second, rid).status_code == 404
    assert first.get(_results_url(rid)).status_code == 200 and (fake_export["cache"] / "adhoc" / "results" / f"{rid}.json").is_file()


def test_delete_is_post_only_same_origin_session_and_owner_protected_and_unknown_ids_fail_safely(owner, fake_export, monkeypatch):
    rid = _rid(_post(owner))
    url = _results_url(rid, "/delete")
    assert owner.get(url).status_code == 405                                                                 # nunca por GET
    assert TestClient(main.app).post(url, data={"pack": "datasphere_core"}, headers=ORIGIN).status_code == 401
    assert owner.post(url, data={"pack": "datasphere_core"}).status_code == 403                              # sin Origin
    assert owner.post(url, data={"pack": "datasphere_core"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert owner.get(_results_url(rid)).status_code == 200                                                   # nada se borró en los intentos rechazados
    for bad in ("0" * 32, "zzz", "../x"):
        assert _delete(owner, bad).status_code in (404, 422)
    monkeypatch.setattr(access, "APP_OWNER", "other@danwor.com")
    assert _delete(owner, rid).status_code == 403


def test_delete_without_js_redirects_back_to_the_module(owner, fake_export):
    rid = _rid(_post(owner))
    r = owner.post(_results_url(rid, "/delete"), data={"pack": "datasphere_core"}, headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/modules/{STEM}?pack=datasphere_core"


def test_delete_touches_nothing_outside_the_single_result_record(owner, fake_export):
    rid = _rid(_post(owner))
    cache = fake_export["cache"]
    before = {p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file()}
    _delete(owner, rid)
    after = {p.relative_to(cache).as_posix() for p in cache.rglob("*") if p.is_file()}
    assert before - after == {f"adhoc/results/{rid}.json"} and after <= before                              # solo desaparece ese registro (perfil + log)
    assert not fake_export["canonical"].exists() or not any(fake_export["canonical"].rglob("*"))             # Catálogo / pool canónico intactos


def test_js_wires_the_delete_through_the_shared_confirmation_and_removes_the_item():
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert "data-dsa-delete" in js and "event.defaultPrevented" in js and "item.remove()" in js and "section.remove()" in js
    assert '"Accept": "application/json"' in js and not re.search(r"localStorage|sessionStorage|innerHTML", js)


# ── Novedades: CTA directo a "Assessment de tu modelo" ─────────────────────────
def test_whats_new_announces_the_assessment_with_a_cta_to_the_real_tool_target(owner):
    from webapp.services import whats_new_service as wn
    items = {e["ref"]: e for e in wn.entries("datasphere_core")}
    e = items["datasphere-assessment"]
    assert e["title"] == "Assessment metodológico para Modelado & Desarrollo" and e["categoria"] == "Modelado" and e["cta"] == "Probar assessment"
    assert e["detail"].startswith("Conectáte a tus modelos de datos")
    assert not re.search(r"scor|puntaje|madurez|CSN|run_export|result_id|directorio", e["detail"], re.I)       # producto, no implementación
    assert e["link"] == f"/modules/{STEM}?pack=datasphere_core&tool=assessment"
    newest = wn.entries("datasphere_core")[0]
    assert newest["ref"] == "datasphere-assessment"                                                          # la más nueva (convención: fecha desc)
    for old_ref in ("data-products-bdc", "d6147d5", "0722a31", "catalogos-danwor"):
        assert old_ref in items                                                                              # las anteriores siguen intactas


def test_whats_new_page_renders_the_card_and_the_cta_label(owner):
    page = owner.get("/whats-new?pack=datasphere_core")
    assert page.status_code == 200
    assert "datasphere-assessment" in page.text and "Probar assessment" in page.text and "tool=assessment" in page.text      # datos de la tarjeta (Alpine)
    assert "Assessment metodol" in page.text and "para Modelado" in page.text                                                  # (la tilde viaja escapada en el JSON de Alpine)
    assert 'x-text="e.cta ? e.cta' in page.text and "Ir al contenido" in page.text                                           # etiqueta por defecto intacta para el resto


def test_cta_lands_on_the_diagnostic_panel_with_the_assessment_tool_selected_and_the_form_reachable(owner):
    r = owner.get(f"/modules/{STEM}?pack=datasphere_core&tool=assessment")
    assert r.status_code == 200 and "x-data=\"{ tool: 'assessment'," in r.text and 'id="dsa-form"' in r.text


def test_cta_does_not_bypass_authentication_or_owner_authorization(monkeypatch):
    anon = TestClient(main.app).get(f"/modules/{STEM}?pack=datasphere_core&tool=assessment", follow_redirects=False)
    assert anon.status_code in (302, 303, 307) and "tool=assessment" not in anon.headers.get("location", "").replace("%3D", "=") or anon.status_code in (302, 303, 307)
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    html = _client("someone@danwor.com").get(f"/modules/{STEM}?pack=datasphere_core&tool=assessment").text
    assert "Acceso restringido" in html and 'id="dsa-form"' not in html                                       # el CTA no da acceso a ejecutar


# ── evidencia NO adquirida visible en el informe HTML ──────────────────────────
_ALL_OPTIONAL = ["replication-flows", "data-flows", "transformation-flows", "task-chains"]


def _report_html(c, rid):
    return c.get(_results_url(rid) + "?pack=datasphere_core").text


def _limitations_section(html):
    return re.search(r'<section class="content-section" id="asr-limitaciones">.*?</section>', html, re.S).group(0)


def test_complete_or_default_scope_renders_nothing_new_in_the_report(owner, fake_export):
    default = _rid(_post(owner))                                                               # sin campos: alcance completo (como hoy)
    explicit = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": _ALL_OPTIONAL}))
    for rid in (default, explicit):
        assert "Evidencia no adquirida" not in _report_html(owner, rid)


def test_limited_scope_shows_the_human_readable_omitted_families_inside_the_limitations_section(owner, fake_export):
    rid = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]}))
    html = _report_html(owner, rid)
    section = _limitations_section(html)
    assert "<strong>Evidencia no adquirida:</strong> Data Flows, Transformation Flows, Task Chains." in section
    assert "su ausencia en este assessment no implica que no exista" in section
    assert html.count("Evidencia no adquirida") == 1                                           # un único lugar; sin banner superior
    paragraph = re.search(r"<strong>Evidencia no adquirida:</strong>[^<]*", section).group(0)
    assert not re.search(r"data-flows|transformation-flows|task-chains|replication-flows", paragraph)     # sin identificadores técnicos
    kpis = html.split("Indicadores del assessment")[1].split("</section>")[0] if "Indicadores del assessment" in html else ""
    assert "Evidencia no adquirida" not in kpis                                                 # no compite con los KPI de cabecera


def test_old_profile_without_evidence_scope_renders_exactly_as_before(owner, fake_export):
    rid = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]}))
    path = fake_export["cache"] / "adhoc" / "results" / f"{rid}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["profile"]["scope"].pop("evidence_scope")                                              # perfil anterior al campo
    path.write_text(json.dumps(rec), encoding="utf-8")
    html = _report_html(owner, rid)
    assert "Evidencia no adquirida" not in html                                                # alcance desconocido NO es alcance reducido
    rec["profile"]["scope"]["evidence_scope"] = None
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert _report_html(owner, rid) == html


def test_reopened_result_shows_its_own_stored_scope_not_the_current_form_state(owner, fake_export):
    limited = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows"]}))
    complete = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": _ALL_OPTIONAL}))
    block = re.search(r'data-dsa-scope>.*?</details>', _page(owner), re.S).group(0)
    assert block.count(" checked>") == 4                                                       # el formulario AHORA está completo...
    assert "Evidencia no adquirida" in _report_html(owner, limited)                            # ...pero el resultado histórico conserva SU alcance
    assert "Evidencia no adquirida" not in _report_html(owner, complete)


def test_json_markdown_and_html_agree_on_the_omitted_families(owner, fake_export):
    rid = _rid(_post(owner, {**VALID, "datasphere_features_submitted": "1", "datasphere_features": ["replication-flows", "task-chains"]}))
    effective = set(json.loads(owner.get(_results_url(rid, "/export.json")).text)["scope"]["evidence_scope"]["effective_features"])
    md = re.search(r"\*\*Evidencia no adquirida:\*\* ([^(]+) \(", owner.get(_results_url(rid, "/export.md")).text).group(1).strip()
    html = re.search(r"<strong>Evidencia no adquirida:</strong> ([^.]+)\.", _report_html(owner, rid)).group(1).strip()
    assert md == html == "Data Flows, Transformation Flows"
    assert "data-flows" not in effective and "transformation-flows" not in effective and "task-chains" in effective      # JSON coherente


# ── REGRESIÓN: el flujo real (extractor REAL sobre un Datasphere simulado) no cambia con el alcance de evidencia ──
@pytest.fixture()
def real_extraction(monkeypatch, tmp_path):
    """A diferencia de ``fake_export`` (que sustituye ``LineageExporter.run``), acá corre el extractor REAL (detección de tipo de raíz,
    compuerta de tipos, alcance de features, recorrido y exportación) contra un DatasphereCli simulado con evidencia sintética."""
    from tests._modeling_assessment_m7_fixtures import write_inbound as _wi
    src = _wi(tmp_path / "ds_src") / "Acme"
    cache = tmp_path / "assessment_cache"
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", cache)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "canonical"))
    svc.reset_boot_state()
    asvc.reset_memory_cache()
    seen = {"requests": [], "results": [], "assess": []}
    monkeypatch.setattr(svc, "cli_available", lambda: True)
    monkeypatch.setattr(dsp.shutil, "which", lambda n: "/usr/bin/npx")
    monkeypatch.setattr(dsp.DatasphereCli, "oauth_preflight", lambda self: None)
    monkeypatch.setattr(dsp.DatasphereCli, "login", lambda self: None)
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: None)
    monkeypatch.setattr(dsp.DatasphereCli, "list_objects", lambda self, space, t: [{"technicalName": p.stem} for p in sorted((src / t).glob("*.json"))] if (src / t).is_dir() else [])
    monkeypatch.setattr(dsp.DatasphereCli, "read_object", lambda self, space, t, n: json.loads((src / t / f"{n}.json").read_text(encoding="utf-8")))
    orig = dsp.run_export

    def spy(req):
        res = orig(req)
        seen["requests"].append(req)
        seen["results"].append(res)
        return res
    monkeypatch.setattr(dsp, "run_export", spy)
    from tools.catalog.modeling_assessment import shadow as ma_shadow
    real_assess = ma_shadow.assess
    monkeypatch.setattr(ma_shadow, "assess", lambda *a, **k: (seen["assess"].append(a), real_assess(*a, **k))[1])
    return seen


def _real_run(root, features=None):
    return svc.run(svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", root_object=root, client="Acme", features=features), SECRET)


@pytest.mark.parametrize("features", [None, ("replication-flows", "data-flows", "transformation-flows", "task-chains"), ("replication-flows",), ()])
def test_valid_terminal_am_still_generates_the_assessment_for_every_scope(real_extraction, features):
    out = _real_run("4AM_ACME", features)
    assert out.state == "SUCCESS" and out.result_id, (out.error_code, out.message)
    res = real_extraction["results"][0]
    assert res.ok and res.root_type == "analytic-model" and real_extraction["assess"]                 # extracción OK -> el motor SÍ corrió
    rec = svc.load_result(out.result_id)
    assert rec["profile"]["scope"]["applicability"] == "APPLICABLE" and rec["profile"]["scope"]["terminal_analytic_model"] == "4AM_ACME"
    assert rec["profile"]["scope"]["evidence_scope"]["effective_features"]                           # el alcance viaja, pero no altera la aplicabilidad


def test_default_and_explicit_all_scopes_are_the_same_effective_extraction(real_extraction):
    _real_run("4AM_ACME", None)
    _real_run("4AM_ACME", ("replication-flows", "data-flows", "transformation-flows", "task-chains"))
    legacy, explicit = real_extraction["results"]
    assert legacy.requested_features == explicit.requested_features and legacy.effective_features == explicit.effective_features
    assert set(legacy.effective_features) == {"analytic-models", "views", "tables", "replication-flows", "data-flows", "transformation-flows", "task-chains"}
    assert real_extraction["requests"][0].features_requested is None
    assert set(real_extraction["requests"][1].features_requested) == set(legacy.effective_features)         # estructurales + las 4 opcionales


def test_evidence_scope_never_changes_terminal_am_closure_or_applicability_on_the_same_pool(real_extraction):
    docs = {}
    for label, feats in (("all", None), ("sub", ("replication-flows",)), ("none", ())):
        out = _real_run("4AM_ACME", feats)
        docs[label] = svc.load_result(out.result_id)["profile"]
    for label in ("sub", "none"):
        for key in ("applicability", "terminal_analytic_model", "closure_size", "closure_artifacts"):
            assert docs[label]["scope"][key] == docs["all"]["scope"][key], (label, key)
    # G1: la cobertura SI depende del alcance declarado, y solo porque una ausencia de productor concluyente es un resultado EVALUADO (NOT_OBSERVABLE);
    # un alcance incompleto (subconjunto o vacio) deja la procedencia NOT_ASSESSED y ambos dan la MISMA cobertura menor. El resto del documento no cambia.
    assert docs["sub"]["summary"]["coverage"] == docs["none"]["summary"]["coverage"]
    assert docs["all"]["summary"]["coverage"]["strict"] >= docs["sub"]["summary"]["coverage"]["strict"]
    for label in ("sub", "none"):
        assert docs[label]["observations"] == docs["all"]["observations"] and docs[label]["review_required"] == docs["all"]["review_required"]


@pytest.mark.parametrize("features", [None, ("replication-flows", "data-flows", "transformation-flows", "task-chains"), ("replication-flows",)])
def test_view_root_runs_the_extraction_and_the_engine_and_produces_a_valid_assessment(real_extraction, features):
    out = _real_run("3VF_P", features)
    assert out.state == "SUCCESS" and out.result_id, (out.error_code, out.message)
    res = real_extraction["results"][0]
    assert res.ok and res.root_type == "view" and real_extraction["assess"]                             # extracción real OK y el motor SÍ corrió
    doc = svc.load_result(out.result_id)["profile"]
    assert doc["assessment_state"] == "ASSESSED" and doc["scope"]["root_type"] == "view" and doc["scope"]["root_object"] == "3VF_P"
    assert doc["scope"]["terminal_analytic_model"] is None and doc["scope"]["applicability"] == "APPLICABLE"
    na = {n["surface_id"] for n in doc["not_applicable"]}
    assert {"am.contract_inventory", "am.association_targets", "am.contract_delta", "rule.am_semantic_underuse_review"} <= na      # solo-AM => N/A


def test_unknown_object_is_reported_as_extraction_not_found_never_as_assessment_applicability(real_extraction):
    out = _real_run("4AM_PUR_PagoFacturaCompra")                                                        # no existe en ESTE Datasphere simulado
    assert out.state == "FAILED" and out.stage == "extraction" and out.error_code == "ROOT_OBJECT_NOT_FOUND"
    assert "evidencia se adquirió" not in out.message and real_extraction["assess"] == []


# ── Novedades: copy exacto ─────────────────────────────────────────────────────
def test_whats_new_final_title_and_body_are_exact(owner):
    from webapp.services import whats_new_service as wn
    e = {x["ref"]: x for x in wn.entries("datasphere_core")}["datasphere-assessment"]
    assert e["title"] == "Assessment metodológico para Modelado & Desarrollo"
    assert e["detail"] == ("Conectáte a tus modelos de datos en cualquiera de nuestros clientes directamente. El Copiloto reconstruye su evidencia y linaje, "
                           "genera un assessment metodológico explicable y permite revisar cobertura, confianza, observaciones y puntos que requieren revisión.")
    assert e["cta"] == "Probar assessment" and e["link"] == f"/modules/{STEM}?pack=datasphere_core&tool=assessment"


# ── ?tool= autoritativo ────────────────────────────────────────────────────────
def test_valid_tool_param_is_authoritative_closing_the_other_tool_groups(owner):
    r = owner.get(f"/modules/{STEM}?pack=datasphere_core&tool=assessment")
    assert re.search(r"copilotLayout\('core', false, '', 'assessment'\)", r.text)
    js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert 'assessment: "diagnostic"' in js                                                               # mapping Assessment→grupo diagnóstico (otras herramientas pueden sumarse)
    assert 'panelOpen: toolGroup ? toolGroup === "decision" : true' in js                                  # decisión se cierra cuando hay una herramienta pedida
    assert 'diagnosticOpen: toolGroup === "diagnostic"' in js and 'gestionOpen: toolGroup === "gestion"' in js
    assert "x-data=\"{ tool: 'assessment'," in r.text and 'id="dsa-form"' in r.text                          # Assessment de tu modelo seleccionado y visible


def test_invalid_or_missing_tool_param_keeps_the_default_navigation(owner):
    for url in (f"/modules/{STEM}?pack=datasphere_core", f"/modules/{STEM}?pack=datasphere_core&tool=evil", f"/modules/{STEM}?pack=datasphere_core&tool=",
                "/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core&tool=assessment"):
        text = owner.get(url).text
        assert re.search(r"copilotLayout\('core', false, '', ''\)", text), url
    js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert 'const toolGroup = TOOL_GROUP[initialTool] || "";' in js                                         # sin grupo => panelOpen=true (default)


def test_unauthorized_user_with_the_tool_link_still_gets_restricted_access(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    html = _client("someone@danwor.com").get(f"/modules/{STEM}?pack=datasphere_core&tool=assessment").text
    assert "Acceso restringido" in html and 'id="dsa-form"' not in html


# ── casillas del alcance de evidencia: espaciado ──────────────────────────────
def test_scope_checkboxes_reuse_the_shared_primitive_and_restore_its_spacing_without_inline_styles(owner):
    block = _scope_form(owner)
    assert block.count('class="dashboard-checkbox-option"') == 4 and "style=" not in block
    css = (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    assert re.search(r"\.dashboard-checkbox-option \{[^}]*gap: 5px", css)                                    # el espaciado es el gap del primitivo
    assert css.count(".tree-picker .dashboard-checkbox-option { display: inline-flex; margin: 0; }") == 1   # restituye el layout que `.tree-picker label` pisaba


# ── pulido post-merge: Total Runtime + "Volver" a su herramienta ───────────────
@pytest.mark.parametrize("seconds,expected", [
    (0, "0.0s"), (47.2, "47.2s"), (59.94, "59.9s"), (59.96, "01m 00s (60.0 s)"), (60, "01m 00s (60.0 s)"), (72.4, "01m 12s (72.4 s)"),
    (412.3, "06m 52s (412.3 s)"), (119.96, "02m 00s (120.0 s)"), (3725.5, "62m 05s (3725.5 s)"), (-3, "0.0s")])
def test_runtime_formatter_contract(seconds, expected):
    assert dsp.format_runtime(seconds) == expected


def test_final_extraction_summary_has_total_runtime_right_before_status_measured_by_the_backend(real_extraction):
    lines: list[str] = []
    out = svc.run(svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", root_object="3VF_P", client="Acme"), SECRET,
                  on_line=lines.append)
    assert out.state == "SUCCESS"
    i = next(k for k, l in enumerate(lines) if l.startswith("Total Runtime:"))
    assert lines[i + 1] == "STATUS: SUCCESS" and lines[i - 1].startswith("Publicación en catálogo")        # inmediatamente antes de STATUS
    assert re.fullmatch(r"Total Runtime: (\d+\.\ds|\d{2,}m \d{2}s \(\d+\.\d s\))", lines[i])
    assert next(k for k, l in enumerate(lines) if "EXTRACCION DE LINAJE FINALIZADA" in l) < i
    res = real_extraction["results"][0]
    shown = float(re.search(r"(\d+\.\d)s?\)?$", lines[i]).group(1))
    assert abs(shown - res.duration_s) < 1.0                                                                # la misma medición del extractor (ExportResult.duration_s)


def test_runtime_is_a_backend_log_line_only_not_js_and_not_part_of_the_assessment(real_extraction):
    js = (REPO / "webapp/static/datasphere_assessment.js").read_text(encoding="utf-8")
    assert "Total Runtime" not in js and "performance.now" not in js and "Date.now" not in js
    out = svc.run(svc.FormValues(host=VALID["host"], token_url=VALID["token_url"], client_id="id", space="S", root_object="4AM_ACME", client="Acme"), SECRET)
    rec = svc.load_result(out.result_id)
    assert "Total Runtime" not in json.dumps(rec["profile"]) and any(l.startswith("Total Runtime:") for l in rec["execution_log"])   # solo en el log


def test_volver_returns_to_the_assessment_tool_with_the_canonical_tool_param(owner, fake_export):
    rid = _rid(_post(owner))
    report = owner.get(_results_url(rid) + "?pack=datasphere_core").text
    href = re.search(r'<a class="catalog-back" href="([^"]+)">← Volver</a>', report).group(1).replace("&amp;", "&")
    assert href == f"/modules/{STEM}?pack=datasphere_core&tool=assessment"
    page = owner.get(href).text
    assert re.search(r"copilotLayout\('core', false, '', 'assessment'\)", page)                              # grupo de diagnóstico abierto
    js = (REPO / "webapp/static/app.js").read_text(encoding="utf-8")
    assert 'panelOpen: toolGroup ? toolGroup === "decision" : true' in js and 'gestionOpen: toolGroup === "gestion"' in js   # decisión/gestión colapsadas
    assert "x-data=\"{ tool: 'assessment'," in page and 'id="dsa-form"' in page                              # herramienta seleccionada y visible
    assert 'value="ACME_SPACE"' in _input(page, "dsa-space") and 'value="Acme"' in _input(page, "dsa-client")  # contexto retenido
    assert "value=" not in _input(page, "dsa-client_secret") and SECRET not in page                           # el secreto sigue ausente


def test_other_report_navigation_is_unchanged(owner, fake_export):
    from webapp.services import assessment_presenter as presenter
    adhoc = presenter.AdhocReport("/b", "/j", "/m")
    status = asvc.AssessmentStatus(asvc.CURRENT, "x", "Acme", "4AM_ACME", generated_at="2026-10-03T00:00:00+00:00", profile=None)
    view = presenter.report_view(status, None, "datasphere_core", adhoc=adhoc)
    assert view["flow_url"] == "/b"                                                                           # el ad-hoc usa SU back
    router_src = (REPO / "webapp/routers/datasphere_assessment.py").read_text(encoding="utf-8")
    assert router_src.count('"tool": "assessment"') == 1                                                      # un único cambio: el back del informe ad-hoc
    cat = (REPO / "webapp/templates/partials/catalog_assessment_report.html").read_text(encoding="utf-8")
    assert "hx-get" in cat and "tool=assessment" not in cat                                                   # el Volver del Catálogo no se tocó

def test_adhoc_report_has_the_same_integrated_controls_table_and_contextual_navigation(owner, fake_export):
    rid = _rid(_post(owner))
    t = owner.get(f"/modules/{STEM}/datasphere-assessment/result/{rid}?pack=datasphere_core").text
    assert " data-asr-report" in t and "Hallazgos en esta área" not in t                                          # marca de informe + lista redundante integrada
    assert '<th>Resultado</th><th>Hallazgos</th><th class="asr-col-action">Acciones</th>' in t
    ids = set(re.findall(r'\sid="([^"]+)"', t))
    hrefs = re.findall(r'<a href="#(asr-[^"]+)"', t)
    assert hrefs and [h for h in hrefs if h not in ids] == []                                                    # sin enlaces huérfanos en la página ad-hoc
    assert 'class="table-action-btn chart-tooltip-anchor" aria-label="Ver evidencia del hallazgo «' in t and "Ver detalle" not in t   # acción icónica (patrón CCHH)
    assert "data-asr-close" in t and "Volver a «" not in t                                                         # X de cierre en el encabezado; sin enlace textual de regreso
    assert "DwAssessmentNav" in (REPO / "webapp/static/app.js").read_text(encoding="utf-8")                      # el cliente que los resuelve se sirve con la app
