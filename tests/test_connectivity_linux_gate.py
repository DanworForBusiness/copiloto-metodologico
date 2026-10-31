"""Gate 7 (F2.1 web) — launcher directo y aislamiento del perfil del CLI en LINUX, con el CLI REAL y SIN tenant.

Solo corre en POSIX con `node` y `node_modules/@sap/datasphere-cli` instalados (en Windows/CI sin CLI se SALTA: un salto NO es
evidencia; el gate queda pendiente hasta ejecutarlo en Linux). Todo es local: el "tenant" es un servidor HTTP falso en 127.0.0.1
y el "perfil real" del usuario se redirige a un directorio temporal (HOME) para que ni un fallo de aislamiento toque el real.

Verifica: (1) el launcher resuelve `direct` (no npx); (2) Node real resuelve `os.homedir()` dentro del perfil aislado;
(3) `login --secrets-file` del CLI real escribe secrets.json SOLO en el workspace efímero, el secreto no viaja por argv ni por
la salida, y el perfil "real" no cambia; (4) un timeout termina REALMENTE el proceso node (no queda vivo) y no deja residuos.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import tools.catalog.DSP_CLI_Export_Lineage as DSP
from tools.catalog.connectivity_governance import (
    Budget, CliConnectionSource, ephemeral_workdir, profile_fingerprint,
)

REPO = Path(DSP.__file__).resolve().parents[2]
TERMINAL_JS = REPO / "node_modules" / "@sap" / "datasphere-cli" / "terminal.js"
SECRET = "SYNTH-linux-gate-S3cret"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("node") is None or not TERMINAL_JS.is_file(),
    reason="gate 7 requiere Linux/POSIX con node y el CLI local de Datasphere instalados",
)


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "operator_home"
    (home / ".@sap" / "datasphere-cli" / ".cache").mkdir(parents=True)
    (home / ".@sap" / "datasphere-cli" / ".cache" / "settings.json").write_text('{"host":"https://other"}')
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture()
def loopback():
    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._json(200, {"access_token": "FAKE-AT", "token_type": "bearer", "expires_in": 3600})

        def do_GET(self):
            self._json(404, {})

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_launcher_is_direct():
    assert DSP.resolve_datasphere_cli_launcher(REPO).mode == "direct"


def test_real_node_homedir_is_isolated(tmp_path, fake_home):
    prof = tmp_path / "prof"
    out = subprocess.run(["node", "-e", "console.log(require('os').homedir())"], env=DSP.isolated_cli_env(prof),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and Path(out.stdout.strip()) == prof


def test_real_cli_login_writes_only_in_ephemeral_profile(fake_home, loopback, monkeypatch):
    argvs = []
    real_run = subprocess.run

    def spy(cmd, **kw):
        argvs.append(list(cmd))
        return real_run(cmd, **kw)
    monkeypatch.setattr(DSP.subprocess, "run", spy)
    real_profile = fake_home / ".@sap" / "datasphere-cli"
    before = profile_fingerprint(real_profile)
    budget = Budget(max_requests=20, max_duration_s=120, max_total_retries=2)
    budget.start()
    with ephemeral_workdir("gate7-") as wd:
        src = CliConnectionSource(host=loopback + "/", client_id="sb-synthetic", client_secret=SECRET,
                                  token_url=loopback + "/oauth/token", probe_space="DEV", timeout=60,
                                  work_dir=str(wd), budget=budget)
        rep = src.preflight_report()
        written = [p for p in wd.rglob("secrets.json")]
        src.close()
    assert rep.status_of("oauth_token").value == "OK"
    assert len(written) == 1                                                     # el CLI escribió SU perfil… en el efímero
    assert not wd.exists()                                                       # …y se borró
    assert profile_fingerprint(real_profile) == before                           # el perfil real no cambió
    assert not any(SECRET in part for cmd in argvs for part in cmd)              # el secreto nunca viajó por argv
    assert SECRET not in json.dumps(rep.to_dict())


def test_timeout_really_kills_the_cli_process(tmp_path):
    launcher = DSP.DatasphereCliLauncher(command=("node", "-e", "process.stdout.write(String(process.pid)); setTimeout(()=>{}, 30000)"),
                                         mode="direct")
    cli = DSP.DatasphereCli(host="https://h.invalid", client_id="i", client_secret=SECRET, token_url="https://t.invalid",
                            work_dir=tmp_path, launcher=launcher, isolated_profile=True)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        cli._run(["spaces", "list"], timeout=1.0)
    assert time.monotonic() - t0 < 10
    time.sleep(0.5)
    alive = subprocess.run(["pgrep", "-f", "setTimeout\\(\\(\\)=>\\{\\}, 30000\\)"], capture_output=True, text=True)
    assert alive.stdout.strip() == "", "el proceso node sobrevivió al timeout"
