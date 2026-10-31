"""test_connectivity_inventory_web.py — G1: extracción AD-HOC de conexiones desde el Catálogo del módulo 01 (F2.1).

OFFLINE: el CLI es FALSO (reemplaza `subprocess.run` del adaptador e imita a @sap/cli-core, que guarda su secrets.json en el home
que resuelve del entorno: USERPROFILE en Windows, HOME en POSIX) y el OAuth HTTP es un stub. Sin tenants. Valores SINTÉTICOS
marcados en host, URL, SID, mandante, Space, conexión, agente, Client ID y secreto.

Gates cubiertos: (1) autorización, mismo origen y validación de host · (2) saneamiento integral de NDJSON, errores y logs ·
(3) no persistencia de secretos ni inventarios · (4) aislamiento y concurrencia entre usuarios/tenants · (5) el módulo 04 no recibe
nada nuevo · (7) launcher `direct` y workspace seguro (fail-closed) · (8) feature flag apagado por defecto.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import tools.catalog.DSP_CLI_Export_Lineage as DSP
import tools.catalog.connectivity_governance as cg
import webapp.main as main
from webapp import access
from webapp.routers import connectivity_inventory as router
from webapp.services import auth_service
from webapp.services import connectivity_inventory_service as svc

STEM = "01_Arquitectura_y_Conectividad"
RUN = f"/modules/{STEM}/connectivity-inventory/run"
ORIGIN = {"Origin": "http://testserver"}
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectivity_governance"

M_HOST = "https://mrkhost.eu10.hcs.cloud.sap"
M_TOKEN = "https://mrkurl.authentication.eu10.hana.ondemand.com/oauth/token"
M_CLIENT_ID = "sb-mrkclientid!b1"
M_SECRET = "MRK-Secret-Value-77"
M_SPACES = ("MRKSPACE_DEV", "MRKSPACE_QAS")
M_CONNS = {"MRKSPACE_DEV": ["MRKCONN_ERP", "MRKCONN_BW"], "MRKSPACE_QAS": ["MRKCONN_ERP", "MRKCONN_JDBC"]}
M_PHYS_HOST, M_SID, M_MANDT, M_AGENT = "erp.mrkphys.corp.invalid", "QZX", "8642", "MRKAGENT_ONPREM"
FORBIDDEN = ("mrk", "QZX", "8642", "hcs.cloud.sap", "ondemand", "oauth/token", "sb-")

VALID = {"host": M_HOST, "token_url": M_TOKEN, "datasphere_client_id": M_CLIENT_ID, "datasphere_client_secret": M_SECRET,
         "client": "Acme", "space_id": list(M_SPACES), "space_env": ["DEV", "QAS"], "probe_connection": "MRKCONN_ERP"}


# ── CLI falso ─────────────────────────────────────────────────────────────────
def _proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _doc(space, name):
    if name == "MRKCONN_JDBC":
        return {"name": name, "space_name": space, "typeId": "GENERICJDBC", "credentialMode": "technicaluser"}
    raw = (FIXTURES / "DESARROLLO__S4ABAP.json").read_text(encoding="utf-8")
    for a, b in (("erp-a-dev.invalid", M_PHYS_HOST), ('"EAD"', f'"{M_SID}"'), ('"100"', f'"{M_MANDT}"'),
                 ("AGENT_SHARED", M_AGENT), ("S4ABAP", name), ("DESARROLLO", space)):
        raw = raw.replace(a, b)
    return json.loads(raw)


def _echo(*extra):
    return " ".join([f"HTTP 403 Forbidden {M_HOST} {M_TOKEN} {M_CLIENT_ID} {M_SECRET}", *extra])


class FakeCli:
    def __init__(self, overrides=None, delay=0.0):
        self.overrides = overrides or {}
        self.delay = delay
        self.calls = []
        self.login_homes = []
        self.preexisting = []

    def __call__(self, cmd, **kw):
        args = list(cmd[1:])
        self.calls.append(args)
        if self.delay:
            time.sleep(self.delay)
        for match, resp in self.overrides.items():
            if tuple(args[:len(match)]) == match:
                if isinstance(resp, BaseException):
                    raise resp
                return resp
        if args[:1] == ["login"]:
            env = kw["env"]
            home = Path(env.get("USERPROFILE" if os.name == "nt" else "HOME") or os.path.expanduser("~"))
            cache = home / ".@sap" / "datasphere-cli" / ".cache"
            self.preexisting.append((cache / "secrets.json").exists())
            self.login_homes.append(str(home))
            content = Path(args[args.index("--secrets-file") + 1]).read_text(encoding="utf-8")
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "secrets.json").write_text("[" + content + "]", encoding="utf-8")
            return _proc(0)
        if args[:2] == ["spaces", "list"]:
            return _proc(0, json.dumps([{"name": s} for s in M_SPACES]))
        if args[:3] == ["spaces", "connections", "list"]:
            sp = args[args.index("--space") + 1]
            return _proc(0, json.dumps([{"name": n} for n in M_CONNS.get(sp, [])]))
        if args[:3] == ["spaces", "connections", "get"]:
            sp, nm = args[args.index("--space") + 1], args[args.index("--name") + 1]
            return _proc(0, json.dumps(_doc(sp, nm)))
        return _proc(1, err=_echo("unknown"))


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Flag encendido + CLI falso + temp/perfil 'real'/cachés redirigidos a tmp. Devuelve un registro del entorno."""
    real = tmp_path / "operator_home"
    (real / ".@sap" / "datasphere-cli" / ".cache").mkdir(parents=True)
    (real / ".@sap" / "datasphere-cli" / ".cache" / "settings.json").write_text('{"host":"https://other"}')
    monkeypatch.setenv("USERPROFILE", str(real))
    monkeypatch.setenv("HOME", str(real))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", tmp_path / "assessment_cache")
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog_local"))
    monkeypatch.setenv(svc.FLAG_ENV, "1")
    monkeypatch.delenv(svc.PILOT_HOSTS_ENV, raising=False)
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    fake = FakeCli()
    monkeypatch.setattr(DSP.subprocess, "run", fake)
    monkeypatch.setattr(DSP, "resolve_datasphere_cli_launcher",
                        lambda root: DSP.DatasphereCliLauncher(command=("fake-cli",), mode="direct"))
    monkeypatch.setattr(DSP.DatasphereCli, "oauth_preflight", lambda self, timeout=30: None)
    activity = []
    monkeypatch.setattr(router, "log_activity", lambda *a, **k: activity.append(k))
    svc._RUNNING.clear()
    return types.SimpleNamespace(real=real, tmp=tmp, root=tmp_path, fake=fake, activity=activity)


def _client(email):
    c = TestClient(main.app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
            {"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def _events(r):
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _done(r):
    return next(e for e in _events(r) if e["t"] == "done")


def _post(c, data=None, headers=None):
    return c.post(RUN, data=data or VALID, headers=ORIGIN if headers is None else headers)


def _shareable(events_or_text, *, hashes=True):
    """Sin ningún valor marcado. `hashes`: además, sin hashes/huellas (salida COMPARTIBLE; en logs/actividad hay ids aleatorios
    legítimos —operation_id, exp de la sesión de prueba— que no derivan de infraestructura)."""
    if isinstance(events_or_text, str):
        text = events_or_text
    else:
        text = json.dumps([{k: v for k, v in e.items() if k != "operation_id"} for e in events_or_text], ensure_ascii=False)
    low = text.lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in low, f"filtró {marker!r}"
    assert "MRK-Secret" not in text
    if hashes:
        assert not re.search(r"\b[0-9a-f]{8,}\b", low)


# ═══════════════ (8) flag apagado por defecto ═══════════════
def test_flag_off_by_default_hides_tool_and_404(monkeypatch, env):
    monkeypatch.delenv(svc.FLAG_ENV)
    assert svc.flag_enabled() is False and svc.tool_enabled(STEM) is False
    owner = _client("owner@danwor.com")
    html = owner.get(f"/modules/{STEM}?pack=datasphere_core&tab=catalogo").text
    assert "dsc-form" not in html and "connectivity_inventory.js" not in html and "Extraer conexiones" not in html
    assert _post(owner).status_code == 404
    assert env.fake.calls == []


def test_flag_on_shows_plus_only_in_module_01(env):
    owner = _client("owner@danwor.com")
    m01 = owner.get(f"/modules/{STEM}?pack=datasphere_core&tab=catalogo").text
    m04 = owner.get("/modules/04_Modelado_y_Desarrollo?pack=datasphere_core&tab=catalogo").text
    assert 'id="dsc-form"' in m01 and "Extraer conexiones de un tenant" in m01 and "declarado" in m01
    assert 'type="password"' in m01 and M_SECRET not in m01
    assert "dsc-form" not in m04 and "connectivity_inventory.js" not in m04                  # (5) el módulo 04 no recibe nada
    assert 'id="dsp-form"' in m04                                                            # su herramienta sigue ahí


# ═══════════════ (1) autorización, mismo origen y validación ═══════════════
def test_authorization_and_origin(monkeypatch, env):
    assert _post(_client(None)).status_code == 401
    owner = _client("owner@danwor.com")
    assert _post(owner, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _post(owner, headers={}).status_code == 403                                       # sin Origin
    assert owner.post("/modules/04_Modelado_y_Desarrollo/connectivity-inventory/run", data=VALID, headers=ORIGIN).status_code == 404
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "trusted@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "reader@danwor.com")
    for email in ("reader@danwor.com", "outsider@danwor.com"):
        c = _client(email)
        assert _post(c).status_code == 403
        assert "dsc-form" not in c.get(f"/modules/{STEM}?pack=datasphere_core&tab=catalogo").text
    trusted = _client("trusted@danwor.com")                                                  # consultor con perfil completo
    assert _post(trusted, data={**VALID, "profile_id": "p1"}).status_code == 403              # perfiles de SharePoint: solo dueño
    assert env.fake.calls == []


@pytest.mark.parametrize("patch,field", [
    ({"host": "https://evil.example.com"}, "host"),
    ({"host": "http://mrkhost.eu10.hcs.cloud.sap"}, "host"),
    ({"token_url": "https://evil.example.com/oauth/token"}, "token_url"),
    ({"datasphere_client_id": "a b"}, "client_id"),
    ({"datasphere_client_secret": ""}, "client_secret"),
    ({"client": ""}, "client"),
    ({"space_id": ["", ""], "space_env": ["", ""]}, "spaces"),
    ({"space_id": ["MRK SPACE"], "space_env": ["DEV"]}, "spaces"),
    ({"space_id": ["MRKSPACE_DEV"], "space_env": ["PRD"]}, "spaces"),
    ({"space_id": ["MRKSPACE_DEV", "mrkspace_dev"], "space_env": ["DEV", "QAS"]}, "spaces"),
    ({"probe_connection": "a,b"}, "probe_connection"),
])
def test_validation_rejects_without_executing(env, patch, field):
    r = _post(_client("owner@danwor.com"), data={**VALID, **patch})
    d = _done(r)
    assert d["state"] == "INVALID" and field in d["errors"] and d["summary"] is None
    assert env.fake.calls == [] and M_SECRET not in r.text


def test_pilot_restriction_is_server_side(monkeypatch, env):
    monkeypatch.setenv(svc.PILOT_HOSTS_ENV, "otro-tenant.eu10.hcs.cloud.sap")
    d = _done(_post(_client("owner@danwor.com")))
    assert d["state"] == "INVALID" and "piloto" in d["errors"]["host"] and env.fake.calls == []
    monkeypatch.setenv(svc.PILOT_HOSTS_ENV, "MRKHOST.eu10.hcs.cloud.sap")                    # habilitado (sin distinguir mayúsculas)
    assert _done(_post(_client("owner@danwor.com")))["state"] == "SUCCESS"
    # una "allowlist" enviada desde el navegador se ignora: no es un campo del formulario
    monkeypatch.setenv(svc.PILOT_HOSTS_ENV, "otro-tenant.eu10.hcs.cloud.sap")
    d = _done(_post(_client("owner@danwor.com"), data={**VALID, "pilot_hosts": "mrkhost.eu10.hcs.cloud.sap"}))
    assert d["state"] == "INVALID"


# ═══════════════ (2) saneamiento integral ═══════════════
def test_success_returns_only_sanitized_summary(env, caplog):
    caplog.set_level(logging.DEBUG)
    r = _post(_client("owner@danwor.com"))
    ev = _events(r)
    d = _done(r)
    assert [e["t"] for e in ev] == ["start", "phase", "done"]                                # sin líneas del extractor
    assert d["state"] == "SUCCESS"
    inv = d["summary"]["inventory"]
    assert inv["status"] == "COMPLETE" and inv["exhaustiveness"] == "UNVERIFIABLE"
    assert inv["coverage"]["extracted_connections"] == 4 and [c["ref"] for c in inv["connections"]] == ["C1", "C2", "C3", "C4"]
    assert inv["connections"][0]["physical_present"]["sysid"] is True
    assert all(v["status"] == "OK" for v in d["summary"]["preflight"].values())
    _shareable(ev)
    _shareable(caplog.text, hashes=False)
    _shareable(json.dumps(env.activity, default=str), hashes=False)


@pytest.mark.parametrize("scenario", ["timeout", "forbidden", "budget", "unexpected", "partial"])
def test_errors_and_aborts_stay_sanitized(monkeypatch, env, caplog, scenario):
    caplog.set_level(logging.DEBUG)
    if scenario == "timeout":
        env.fake.overrides = {("login",): subprocess.TimeoutExpired(["fake-cli", "login", M_HOST, M_SECRET], 5,
                                                                     output=M_SECRET, stderr=M_HOST)}
    elif scenario == "forbidden":
        env.fake.overrides = {("spaces", "connections", "list"): _proc(1, err=_echo(*M_SPACES))}
    elif scenario == "budget":
        monkeypatch.setattr(svc, "LIMITS", {"max_requests": 6, "max_total_retries": 6, "max_duration_s": 600.0})
    elif scenario == "unexpected":
        def boom(*a, **k):
            raise RuntimeError(f"boom {M_HOST} {M_SECRET} {M_SID} {M_AGENT}")
        monkeypatch.setattr(cg, "run_probe", boom)
    elif scenario == "partial":
        env.fake.overrides = {("spaces", "connections", "get", "--space", "MRKSPACE_QAS"): _proc(1, err=_echo(M_PHYS_HOST, M_AGENT))}
    r = _post(_client("owner@danwor.com"))
    d = _done(r)
    expected = {"timeout": ("FAILED", "PREFLIGHT_INSUFFICIENT"), "forbidden": ("FAILED", "PREFLIGHT_INSUFFICIENT"),
                "budget": ("FAILED", "REQUESTS_EXCEEDED"), "unexpected": ("FAILED", "UNEXPECTED_ERROR"),
                "partial": ("PARTIAL", None)}[scenario]
    assert (d["state"], d["error_code"]) == expected
    if scenario == "budget":
        assert "inventory" not in d["summary"]                                                # aborto: nunca COMPLETE
    if scenario == "timeout":
        assert d["summary"]["preflight"]["oauth_token"] == {"status": "FAILED", "code": "TIMEOUT"}
    _shareable(_events(r))
    _shareable(caplog.text, hashes=False)
    _shareable(json.dumps(env.activity, default=str), hashes=False)


# ═══════════════ (3) no persistencia ═══════════════
def test_nothing_persists(env):
    for _ in range(2):
        assert _done(_post(_client("owner@danwor.com")))["state"] == "SUCCESS"
    leftovers = [p for p in env.tmp.iterdir()]
    assert leftovers == []                                                                   # workspaces efímeros borrados
    files = [p for p in env.root.rglob("*") if p.is_file()]
    assert files == [env.real / ".@sap" / "datasphere-cli" / ".cache" / "settings.json"]      # solo lo que ya existía
    assert not (env.root / "catalog_local").exists() and not (env.root / "assessment_cache").exists()
    for p in files:
        assert M_SECRET not in p.read_text(encoding="utf-8", errors="replace")


# ═══════════════ (4) aislamiento y concurrencia ═══════════════
def test_each_run_gets_its_own_profile_and_real_profile_is_untouched(env):
    before = cg.profile_fingerprint(env.real / ".@sap" / "datasphere-cli")
    for email in ("owner@danwor.com", "owner@danwor.com"):
        _post(_client(email))
    assert len(env.fake.login_homes) == 2 and len(set(env.fake.login_homes)) == 2         # perfil propio por ejecución
    assert all(Path(h).is_relative_to(env.tmp) for h in env.fake.login_homes)
    assert env.fake.preexisting == [False, False]                                            # ninguna hereda sesión/tokens
    assert cg.profile_fingerprint(env.real / ".@sap" / "datasphere-cli") == before


def test_parallel_runs_for_different_users_and_tenants_are_isolated(env):
    env.fake.delay = 0.02
    other = svc.ExtractionValues.from_form(host="https://otro.eu10.hcs.cloud.sap", token_url=M_TOKEN, client_id=M_CLIENT_ID,
                                           client="Beta", space_ids=["MRKSPACE_DEV"], space_envs=["DEV"])
    mine = svc.ExtractionValues.from_form(host=M_HOST, token_url=M_TOKEN, client_id=M_CLIENT_ID, client="Acme",
                                          space_ids=["MRKSPACE_DEV"], space_envs=["DEV"])
    results = {}
    threads = [threading.Thread(target=lambda k, v, u: results.__setitem__(k, svc.run(v, M_SECRET, user_email=u, sleep=lambda s: None)),
                                args=(k, v, u)) for k, v, u in (("a", mine, "a@danwor.com"), ("b", other, "b@danwor.com"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert {r.state for r in results.values()} == {"SUCCESS"}
    assert len(set(env.fake.login_homes)) == 2 and env.fake.preexisting == [False, False]
    assert list(env.tmp.iterdir()) == []


def test_single_flight_and_capacity(env):
    values = svc.ExtractionValues.from_form(host=M_HOST, token_url=M_TOKEN, client_id=M_CLIENT_ID, client="Acme",
                                            space_ids=["MRKSPACE_DEV"], space_envs=["DEV"])
    lock = svc._lock_for("a@danwor.com", M_HOST)
    lock.acquire()
    try:
        assert svc.run(values, M_SECRET, user_email="a@danwor.com").error_code == "OPERATION_ALREADY_RUNNING"
        assert svc.run(values, M_SECRET, user_email="b@danwor.com").state == "SUCCESS"         # otro usuario: no se bloquea
    finally:
        lock.release()
    held = [svc._CAPACITY.acquire(blocking=False) for _ in range(svc.MAX_CONCURRENT)]
    try:
        assert all(held) and svc.run(values, M_SECRET, user_email="c@danwor.com").error_code == "CAPACITY_EXCEEDED"
    finally:
        for _ in held:
            svc._CAPACITY.release()


# ═══════════════ (7) launcher directo y workspace seguro (fail-closed) ═══════════════
def test_non_direct_launcher_fails_closed(monkeypatch, env):
    monkeypatch.setattr(svc, "_launcher_mode", lambda: "npx")
    d = _done(_post(_client("owner@danwor.com")))
    assert d["error_code"] == "CLI_LAUNCHER_NOT_DIRECT" and env.fake.calls == []


def test_unresolvable_cli_fails_closed(monkeypatch, env):
    def unavailable(root):
        raise DSP.DatasphereCliError("DATASPHERE_CLI_UNAVAILABLE")
    monkeypatch.setattr(DSP, "resolve_datasphere_cli_launcher", unavailable)
    assert _done(_post(_client("owner@danwor.com")))["error_code"] == "CLI_UNAVAILABLE" and env.fake.calls == []


def test_workspace_under_persisted_dir_fails_closed(monkeypatch, env):
    monkeypatch.setattr(svc, "_workspace_safe", lambda base: False)
    assert _done(_post(_client("owner@danwor.com")))["error_code"] == "WORKSPACE_UNSAFE" and env.fake.calls == []


def test_workspace_guard_uses_persisted_dirs_and_catalog(monkeypatch, tmp_path):
    from core import remote_store
    persisted = tmp_path / "persisted"
    catalog = tmp_path / "catalog_local"
    monkeypatch.setattr(remote_store, "_persist_dirs", lambda: [("x", str(persisted))])
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(catalog))
    assert svc._workspace_safe(tmp_path / "tmp") is True
    assert svc._workspace_safe(persisted / "tmp") is False                                # lo subiría mirror_up()
    assert svc._workspace_safe(catalog / "tmp") is False

    def broken():
        raise RuntimeError("no se puede leer la configuración")
    monkeypatch.setattr(remote_store, "_persist_dirs", broken)
    assert svc._workspace_safe(tmp_path / "tmp") is False                                 # no verificable ⇒ no se ejecuta


def test_real_launcher_resolution_is_direct_in_this_checkout():
    # El checkout tiene node + node_modules/@sap/datasphere-cli: el launcher real debe resolver 'direct' (sin npx).
    repo = Path(DSP.__file__).resolve().parents[2]
    if not (repo / "node_modules" / "@sap" / "datasphere-cli" / "terminal.js").is_file() or not __import__("shutil").which("node"):
        pytest.skip("CLI local no instalado en este entorno")
    assert DSP.resolve_datasphere_cli_launcher(repo).mode == "direct"


def test_provisional_tenant_key_is_not_a_governed_identity():
    assert svc.provisional_tenant_key("https://MRKHOST.eu10.hcs.cloud.sap/") == "provisional-host:mrkhost.eu10.hcs.cloud.sap"
