"""F2.1c — correcciones bloqueantes de seguridad de la sonda DEV/QA (H1–H4), OFFLINE.

Sin tenant ni red: `subprocess.run` del CLI se reemplaza por un CLI FALSO con reloj simulado (o por un proceso
local que solo duerme), y el OAuth HTTP por un stub. El "perfil real" del operador se redirige a un directorio
temporal con monkeypatch, de modo que ni un fallo del aislamiento puede tocar el perfil real de quien corre los
tests. Credenciales SINTÉTICAS.

- H1: ningún error/excepción/traceback/resultado expone el secreto (argv en lista, TimeoutExpired, stderr del
  CLI con eco); login por `--secrets-file` efímero (el secreto no viaja por argv).
- H2: perfil del CLI aislado de verdad (USERPROFILE+HOME+…; entorno mínimo); escrituras del CLI caen en el
  workspace efímero, nunca en el perfil real; limpieza verificada; detección runtime de cambios en el real.
- H3: una sola política de reintentos (con espera presupuestada) en orquestador y fuente; llamadas FÍSICAS
  medidas una sola vez (preflight incluido); Retry-After/backoff respetados y cobrados.
- H4: cota de duración ANTES de cada llamada/espera (timeout = restante); corte ⇒ ABORTED, nunca COMPLETE.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import types
from pathlib import Path

import pytest

import tools.catalog.DSP_CLI_Export_Lineage as DSP
from tools.catalog.connectivity_governance import (
    Budget,
    BudgetedSource,
    BudgetError,
    CheckStatus,
    CliConnectionSource,
    SafetyError,
    SimulatedConnectionSource,
    ephemeral_workdir,
    profile_fingerprint,
    remove_tree_verified,
    run_probe,
    sanitize,
)
from tools.catalog.DSP_CLI_Export_Lineage import isolated_cli_env, redact_cli_argv

SECRET = "SYNTH-Cl1ent\\S3cret!q9"          # sintético; incluye "\" para ejercitar el escapado de repr()
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "connectivity_governance"
DATA = {"DESARROLLO": ["BW_DS", "S4ABAP", "S4PREM"],
        "CALIDAD": ["BW_DS", "PostgreSQL", "S4ABAP", "S4PREM", "SQL_SERVER_2019_onpremise"]}
GOV_MAP = {"DESARROLLO": "DEV", "CALIDAD": "QAS"}
PREFLIGHT_PHYSICAL = 5        # oauth HTTP + login + spaces list + connections list + connections get
HAPPY_PHYSICAL = PREFLIGHT_PHYSICAL + len(DATA) + sum(len(v) for v in DATA.values())   # 5 + 2 + 8 = 15


# ── CLI falso con reloj simulado ──────────────────────────────────────────────
class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _cli_home(env):
    """Resolución del home como Node/libuv: Windows → USERPROFILE; POSIX → HOME; si falta, el perfil REAL."""
    key = "USERPROFILE" if os.name == "nt" else "HOME"
    return Path(env.get(key) or os.environ[key])


class FakeCli:
    """Reemplaza `subprocess.run`. `script(ds_args)` → (duración_simulada, proc) o lanza. Simula un CLI que,
    en `login`, persiste su secrets.json bajo `homedir()/.@sap/datasphere-cli/.cache` (como @sap/cli-core)."""
    def __init__(self, clock, script=None):
        self.clock = clock
        self.script = script or (lambda a: None)
        self.calls = []                # (ds_args, timeout, t_al_llamar)
        self.login_secret_files = []   # contenido del --secrets-file visto durante la llamada
        self.preexisting_secrets = []  # ¿había secrets.json en el perfil ANTES del login?
        self.login_homes = []          # home que resolvió el CLI en cada login (dónde escribe su perfil)
        self.ignore_timeout = False

    def __call__(self, cmd, **kw):
        ds_args = list(cmd[1:])
        timeout = kw.get("timeout")
        self.calls.append((ds_args, timeout, self.clock.t))
        out = self.script(ds_args)
        dur, proc = out if out is not None else _default(ds_args)
        if timeout is not None and dur > timeout and not self.ignore_timeout:
            self.clock.t += timeout
            # Igual que subprocess.run: el TimeoutExpired original lleva el argv COMPLETO y salida parcial.
            raise subprocess.TimeoutExpired(cmd, timeout, output="partial", stderr="partial")
        self.clock.t += dur
        if ds_args[:1] == ["login"] and proc.returncode == 0:
            self._persist_login(ds_args, kw["env"])
        return proc

    def _persist_login(self, ds_args, env):
        self.login_homes.append(str(_cli_home(env)))
        cache = _cli_home(env) / ".@sap" / "datasphere-cli" / ".cache"
        self.preexisting_secrets.append((cache / "secrets.json").exists())
        if "--secrets-file" in ds_args:
            content = Path(ds_args[ds_args.index("--secrets-file") + 1]).read_text(encoding="utf-8")
            self.login_secret_files.append(content)
            secret = json.loads(content)
        else:
            secret = {"client_secret": ds_args[ds_args.index("--client-secret") + 1]}
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "secrets.json").write_text(json.dumps([secret]), encoding="utf-8")


def _default(ds_args):
    if ds_args[:1] == ["login"]:
        return 0.2, _proc(0)
    if ds_args[:2] == ["spaces", "list"]:
        return 0.1, _proc(0, json.dumps([{"name": s} for s in DATA]))
    if ds_args[:3] == ["spaces", "connections", "list"]:
        space = ds_args[ds_args.index("--space") + 1]
        return 0.1, _proc(0, json.dumps([{"name": n} for n in DATA.get(space, [])]))
    if ds_args[:3] == ["spaces", "connections", "get"]:
        space = ds_args[ds_args.index("--space") + 1]
        name = ds_args[ds_args.index("--name") + 1]
        return 0.1, _proc(0, (FIXTURES / f"{space}__{name}.json").read_text(encoding="utf-8"))
    return 0.1, _proc(1, stderr="unexpected command")


@pytest.fixture
def real_profile(tmp_path, monkeypatch):
    """Perfil 'real' del operador redirigido a tmp: el aislamiento se mide contra ESTE directorio."""
    real = tmp_path / "operator_profile"
    (real / ".@sap" / "datasphere-cli" / ".cache").mkdir(parents=True)
    (real / ".@sap" / "datasphere-cli" / ".cache" / "settings.json").write_text('{"host":"https://other"}')
    monkeypatch.setenv("USERPROFILE", str(real))
    monkeypatch.setenv("HOME", str(real))
    return real


def _source(monkeypatch, tmp_path, *, clock, script=None, budget=None, legacy=False, work_dir=None, **kw):
    fake = FakeCli(clock, script)
    monkeypatch.setattr(DSP.subprocess, "run", fake)
    monkeypatch.setattr(DSP, "resolve_datasphere_cli_launcher",
                        lambda root: DSP.DatasphereCliLauncher(command=("fake-cli",), mode="direct"))
    src = CliConnectionSource(
        host="https://h.devqa.invalid/", client_id="sb-synthetic", client_secret=SECRET,
        token_url="https://t.devqa.invalid/oauth/token", probe_space="CALIDAD", probe_name="BW_DS",
        timeout=kw.pop("timeout", 120.0),
        work_dir=str(work_dir or (tmp_path / "wd")), budget=budget,
        isolated_profile=not legacy, use_secrets_file=not legacy, **kw)
    fake.oauth_calls = []

    def oauth(timeout=30):
        fake.oauth_calls.append((timeout, clock.t))
        clock.t += 0.3
    src._cli_obj().oauth_preflight = oauth
    return src, fake


def _budget(clock, *, requests=1000, duration=300.0, retries=20):
    return Budget(max_requests=requests, max_duration_s=duration, max_total_retries=retries, clock=clock)


def _probe(src, budget, clock, real=None, **kw):
    return run_probe(src, budget, client="probe-test", tenant_id="t-devqa", tenant_host=src.host,
                     space_environment_map=GOV_MAP, expected_spaces=list(DATA), sleep=clock.sleep,
                     real_profile=real, **kw)


def _physical(fake):
    return len(fake.calls) + len(fake.oauth_calls)


def _rate_limited_then_ok(match, times, retry_after):
    state = {"n": 0}
    def script(ds_args):
        if ds_args[:len(match)] == match and state["n"] < times:
            state["n"] += 1
            return 0.1, _proc(1, stderr=f"HTTP 429 Too Many Requests; Retry-After: {retry_after}")
        return None
    return script


# ═════════════════════════════ H1 — secretos ════════════════════════════════
def test_h1_sanitize_redacts_secret_in_argv_list_repr():
    cmd = ["node", "terminal.js", "login", "--client-id", "sb-x", "--client-secret", SECRET, "--host", "h"]
    for text in (repr(cmd), str(subprocess.TimeoutExpired(cmd, 5)),
                 str(subprocess.CalledProcessError(1, cmd)), " ".join(cmd),
                 f"client_secret={SECRET}&grant_type=x", json.dumps({"client_secret": SECRET})):
        assert "Cl1ent" not in sanitize(text), text


def test_h1_sanitize_scrubs_known_secret_in_any_format():
    text = f"raw<{SECRET}> repr={SECRET!r} json={json.dumps(SECRET)}"
    out = sanitize(text, (SECRET,))
    assert "Cl1ent" not in out and "[REDACTED]" in out


def test_h1_redact_cli_argv():
    red = redact_cli_argv(["login", "--client-secret", SECRET, "--access-token=abc", "--host", "h"])
    assert red == ["login", "--client-secret", "[REDACTED]", "--access-token=[REDACTED]", "--host", "h"]


def test_h1_real_subprocess_timeout_does_not_carry_secret(tmp_path):
    # Camino EXACTO del hallazgo: login por argv (modo histórico) + TimeoutExpired REAL de subprocess.run.
    launcher = DSP.DatasphereCliLauncher(command=(sys.executable, "-c", "import time; time.sleep(30)"), mode="direct")
    cli = DSP.DatasphereCli(host="https://h.devqa.invalid", client_id="sb-x", client_secret=SECRET,
                            token_url="https://t.devqa.invalid/oauth/token", work_dir=tmp_path, launcher=launcher)
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        cli.login(timeout=0.3)
    e = ei.value
    tb = "".join(traceback.format_exception(e))
    for blob in (str(e), repr(e), repr(e.cmd), tb):
        assert "Cl1ent" not in blob
    assert e.__context__ is None and e.__cause__ is None      # sin la excepción original encadenada
    assert e.output is None and e.stderr is None               # sin salida parcial del proceso


def test_h1_preflight_detail_on_login_timeout_has_no_secret(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    src, fake = _source(monkeypatch, tmp_path, clock=clock, legacy=True, timeout=5.0,
                        script=lambda a: (999.0, _proc(0)) if a[:1] == ["login"] else None)
    rep = src.preflight_report()
    assert rep.status_of("oauth_token") == CheckStatus.FAILED
    assert "Cl1ent" not in json.dumps(rep.to_dict())
    assert rep.to_dict()["oauth_token"]["detail"] == "la operación del CLI excedió el tiempo límite"


def test_h1_cli_stderr_echoing_secret_is_scrubbed(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    echo = f"login failed for secret<{SECRET}> --client-secret {SECRET} client_secret={SECRET}"
    src, fake = _source(monkeypatch, tmp_path, clock=clock,
                        script=lambda a: (0.1, _proc(1, stderr=echo)) if a[:1] == ["login"] else None)
    rep = src.preflight_report()
    assert rep.status_of("oauth_token") == CheckStatus.FAILED
    assert "Cl1ent" not in json.dumps(rep.to_dict())


def test_h1_login_uses_ephemeral_secrets_file_not_argv(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    src, fake = _source(monkeypatch, tmp_path, clock=clock)
    assert src.preflight_report().status_of("oauth_token") == CheckStatus.OK
    login = [a for a, _, _ in fake.calls if a[:1] == ["login"]]
    assert len(login) == 1 and "--secrets-file" in login[0] and "--client-secret" not in login[0]
    assert all("Cl1ent" not in part for args, _, _ in fake.calls for part in args)      # nunca en argv
    assert json.loads(fake.login_secret_files[0])["client_secret"] == SECRET            # el CLI lo recibió
    assert not Path(login[0][login[0].index("--secrets-file") + 1]).exists()            # y se borró


def test_h1_secrets_file_removed_even_when_login_fails(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    src, fake = _source(monkeypatch, tmp_path, clock=clock,
                        script=lambda a: (0.1, _proc(1, stderr="401")) if a[:1] == ["login"] else None)
    src.preflight_report()
    login = next(a for a, _, _ in fake.calls if a[:1] == ["login"])
    assert not Path(login[login.index("--secrets-file") + 1]).exists()


@pytest.mark.parametrize("scenario", ["ok", "preflight_fail", "aborted"])
def test_h1_probe_outcome_never_contains_secret(monkeypatch, tmp_path, real_profile, scenario):
    clock = Clock()
    script = {"ok": None,
              "preflight_fail": lambda a: (0.1, _proc(1, stderr=f"bad {SECRET}")) if a[:1] == ["login"] else None,
              "aborted": lambda a: (999.0, _proc(0)) if a[:3] == ["spaces", "connections", "get"] else None}[scenario]
    budget = _budget(clock, duration=30.0)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, script=script, budget=budget)
    out = _probe(src, budget, clock)
    assert "Cl1ent" not in json.dumps(out.__dict__, default=str)


# ═════════════════════════ H2 — aislamiento del perfil ══════════════════════
def test_h2_isolated_env_redirects_profile_and_drops_injectable_vars(tmp_path, monkeypatch):
    for k, v in {"CLI_LEGACY_CACHE_LOCATION": "true", "NODE_CONFIG": '{"tenant":{"host":"https://prd"}}',
                 "NODE_OPTIONS": "--require evil.js", "CLIENT_SECRET": "x", "ACCESS_TOKEN": "x", "HOST": "https://prd",
                 "SECRETS_FILE": "x", "OPTIONS_FILE": "x", "HTTPS_PROXY": "http://proxy:8080"}.items():
        monkeypatch.setenv(k, v)
    prof = tmp_path / "prof"
    env = isolated_cli_env(prof)
    assert env["USERPROFILE"] == str(prof) and env["HOME"] == str(prof)
    for k in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "NODE_CONFIG_DIR"):
        assert Path(env[k]).is_relative_to(prof)
    for k in ("CLI_LEGACY_CACHE_LOCATION", "NODE_CONFIG", "NODE_OPTIONS", "CLIENT_SECRET", "ACCESS_TOKEN",
              "HOST", "SECRETS_FILE", "OPTIONS_FILE"):
        assert k not in env
    assert env["HTTPS_PROXY"] == "http://proxy:8080"


def test_h2_root_cause_home_only_leaves_userprofile_real(tmp_path, real_profile):
    legacy = DSP.DatasphereCli(host="https://h", client_id="i", client_secret=SECRET, token_url="https://t",
                               work_dir=tmp_path / "w", launcher=DSP.DatasphereCliLauncher(("x",), "direct"))
    assert legacy.env["USERPROFILE"] == str(real_profile)      # HOME solo NO aísla en Windows (Node usa USERPROFILE)
    iso = DSP.DatasphereCli(host="https://h", client_id="i", client_secret=SECRET, token_url="https://t",
                            work_dir=tmp_path / "w2", launcher=DSP.DatasphereCliLauncher(("x",), "direct"),
                            isolated_profile=True)
    assert Path(iso.env["USERPROFILE"]).is_relative_to(tmp_path / "w2")


@pytest.mark.skipif(shutil.which("node") is None, reason="requiere node para medir os.homedir() real")
def test_h2_real_node_homedir_resolves_inside_isolated_profile(tmp_path, real_profile):
    # Node REAL resolviendo su home con el entorno aislado y escribiendo como @sap/cli-core.
    js = ("const p=require('path'),fs=require('fs');const d=p.join(require('os').homedir(),'.@sap','datasphere-cli','.cache');"
          "fs.mkdirSync(d,{recursive:true});fs.writeFileSync(p.join(d,'secrets.json'),'[]');console.log(d)")
    prof = tmp_path / "prof"
    before = profile_fingerprint(real_profile)
    r = subprocess.run(["node", "-e", js], env=isolated_cli_env(prof), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()).is_relative_to(prof)
    assert (prof / ".@sap" / "datasphere-cli" / ".cache" / "secrets.json").exists()
    assert profile_fingerprint(real_profile) == before


def test_h2_cli_writes_land_in_ephemeral_profile_and_are_cleaned(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    before = profile_fingerprint(real_profile)
    with ephemeral_workdir("h2-") as wd:
        src, fake = _source(monkeypatch, tmp_path, clock=clock, work_dir=wd)
        assert src.preflight_report().status_of("oauth_token") == CheckStatus.OK
        written = list(wd.rglob("secrets.json"))
        assert len(written) == 1 and json.loads(written[0].read_text(encoding="utf-8"))[0]["client_secret"] == SECRET
        src.close()
    assert not wd.exists()                                       # perfil efímero borrado
    assert profile_fingerprint(real_profile) == before            # perfil real intacto
    assert not list(real_profile.rglob("secrets.json"))


def test_h2_each_run_starts_with_empty_profile_no_token_reuse(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    for i in range(2):                                           # dos clientes/ejecuciones consecutivas
        with ephemeral_workdir(f"h2run{i}-") as wd:
            src, fake = _source(monkeypatch, tmp_path, clock=clock, work_dir=wd)
            src.preflight_report()
            assert fake.preexisting_secrets == [False]           # nada heredado de la ejecución anterior
            src.close()


def test_h2_cleanup_on_failure(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    captured = {}
    with pytest.raises(RuntimeError):
        with ephemeral_workdir("h2fail-") as wd:
            captured["wd"] = wd
            src, fake = _source(monkeypatch, tmp_path, clock=clock, work_dir=wd)
            src.preflight_report()
            assert list(wd.rglob("secrets.json"))
            raise RuntimeError("falla a mitad de la extracción")
    assert not captured["wd"].exists()


def test_h2_cleanup_failure_is_reported_not_hidden(monkeypatch, tmp_path):
    d = tmp_path / "stuck"
    (d / ".cache").mkdir(parents=True)
    (d / ".cache" / "secrets.json").write_text("[]")
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)          # simula archivo retenido
    with pytest.raises(SafetyError) as ei:
        remove_tree_verified(d, sleep=lambda s: None)
    assert ei.value.code == "WORKDIR_CLEANUP_FAILED"


def test_h2_owned_workdir_removed_and_secret_refs_dropped_on_close(monkeypatch, tmp_path, real_profile):
    monkeypatch.setattr(DSP, "resolve_datasphere_cli_launcher",
                        lambda root: DSP.DatasphereCliLauncher(command=("fake-cli",), mode="direct"))
    src = CliConnectionSource(host="https://h", client_id="sb-x", client_secret=SECRET, token_url="https://t")
    cli = src._cli_obj()
    owned = src._owned_dir
    assert owned is not None and owned.exists()
    src.close()
    assert not owned.exists() and src.client_secret is None and cli.client_secret is None


def test_h2_legacy_mode_write_target_depends_on_platform(monkeypatch, tmp_path, real_profile):
    # Modo HISTÓRICO (isolated_profile=False): solo redefine HOME. Node resuelve el home con USERPROFILE en Windows y con HOME en
    # POSIX ⇒ en Windows el CLI escribe en el perfil REAL (la causa raíz de H2); en Linux cae en el workspace. Expectativa explícita
    # por plataforma (no un skip): ambas ramas se verifican, una en cada sistema (Windows local, Linux en CI).
    clock = Clock()
    budget = _budget(clock)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, legacy=True)
    out = _probe(src, budget, clock, real=real_profile / ".@sap" / "datasphere-cli")
    login_home = Path(fake.login_homes[0])
    if os.name == "nt":
        assert login_home == real_profile                                    # USERPROFILE no se redirige
        assert out.real_profile_modified is True and out.exit_code == 5      # …y la sonda lo detecta
    else:
        assert login_home.is_relative_to(tmp_path / "wd")                    # HOME redirigido al workspace
        assert out.real_profile_modified is False and out.exit_code == 0


@pytest.mark.parametrize("tamper", ["new_file", "modify_existing", "delete_existing"])
def test_h2_probe_detects_deliberate_real_profile_write(monkeypatch, tmp_path, real_profile, tamper):
    # Detección POSITIVA, en cualquier plataforma: un CLI que ignorara el aislamiento y tocara el perfil real durante la ejecución
    # se detecta (sin falsos negativos: archivo nuevo, archivo existente modificado con el mismo tamaño, archivo borrado).
    cache = real_profile / ".@sap" / "datasphere-cli" / ".cache"
    settings = cache / "settings.json"

    def intruder(ds_args):
        if ds_args[:1] == ["login"]:
            if tamper == "new_file":
                (cache / "secrets.json").write_text("[]", encoding="utf-8")
            elif tamper == "modify_existing":
                old = settings.read_text(encoding="utf-8")
                time.sleep(0.02)                                             # mtime distinto aunque el tamaño no cambie
                settings.write_text(old[::-1], encoding="utf-8")
            else:
                settings.unlink()
        return None                                                          # el resto del CLI falso responde normal
    clock = Clock()
    budget = _budget(clock)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=intruder)   # modo AISLADO (default)
    out = _probe(src, budget, clock, real=real_profile / ".@sap" / "datasphere-cli")
    assert out.real_profile_modified is True and out.exit_code == 5


def test_h2_isolated_mode_never_touches_real_profile(monkeypatch, tmp_path, real_profile):
    # Modo AISLADO (default del adaptador de conectividad): en TODAS las plataformas el CLI escribe solo en el workspace efímero.
    clock = Clock()
    budget = _budget(clock)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, work_dir=tmp_path / "wd2")
    real = real_profile / ".@sap" / "datasphere-cli"
    before = profile_fingerprint(real)
    out = _probe(src, budget, clock, real=real)
    assert all(Path(h).is_relative_to(tmp_path / "wd2") for h in fake.login_homes) and fake.login_homes
    assert out.real_profile_modified is False and out.exit_code == 0
    assert profile_fingerprint(real) == before


# ═══════════════════════ H3 — reintentos y presupuestos ═════════════════════
def test_h3_happy_path_counts_every_physical_call_once(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget)
    out = _probe(src, budget, clock)
    assert out.result == "OK" and out.inventory["status"] == "COMPLETE"
    assert out.inventory["exhaustiveness"] == "UNVERIFIABLE"
    assert budget.requests_used == _physical(fake) == HAPPY_PHYSICAL       # preflight incluido, sin doble conteo
    assert sum(1 for a, _, _ in fake.calls if a[:1] == ["login"]) == 1     # preflight NO se re-ejecuta


def test_h3_get_connection_respects_retry_after_and_charges_retries(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock)
    script = _rate_limited_then_ok(["spaces", "connections", "get", "--space", "DESARROLLO", "--name", "S4ABAP"], 2, 7)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=script)
    out = _probe(src, budget, clock)
    assert out.result == "OK" and out.inventory["status"] == "COMPLETE"
    assert clock.sleeps == [7.0, 7.0]                                     # Retry-After respetado (antes: 0 esperas)
    assert budget.retries_used == 2
    assert budget.requests_used == _physical(fake) == HAPPY_PHYSICAL + 2


def test_h3_list_connections_retry_counted_once_per_physical_call(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock)
    script = _rate_limited_then_ok(["spaces", "connections", "list", "--space", "DESARROLLO"], 1, 3)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=script)
    out = _probe(src, budget, clock)
    assert out.result == "OK"
    assert clock.sleeps == [3.0] and budget.retries_used == 1
    assert budget.requests_used == _physical(fake) == HAPPY_PHYSICAL + 1


def test_h3_backoff_applied_and_charged_without_retry_after(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock)
    state = {"n": 0}
    def script(a):
        if a[:3] == ["spaces", "connections", "get"] and "S4PREM" in a and state["n"] == 0:
            state["n"] += 1
            return 50.0, _proc(0)                     # excede el timeout POR OPERACIÓN (10s) → TIMEOUT retriable
        return None
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=script, timeout=10.0)
    out = _probe(src, budget, clock)
    assert out.result == "OK" and out.inventory["status"] == "COMPLETE"
    assert clock.sleeps == [0.5] and budget.retries_used == 1             # backoff base, cobrado


def test_h3_total_retry_budget_aborts(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock, retries=3)
    script = _rate_limited_then_ok(["spaces", "connections", "get"], 10_000, 0.1)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=script)
    # el preflight también sufre el 429 en read_detail (FAILED, sin reintento) → igual autoriza (oauth + list OK)
    out = _probe(src, budget, clock)
    assert out.result == "ABORTED" and out.abort_code == "RETRIES_EXCEEDED"
    assert out.inventory is None and out.exit_code == 4


def test_h3_request_budget_counts_physical_calls_including_preflight(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock, requests=PREFLIGHT_PHYSICAL + 1)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget)
    out = _probe(src, budget, clock)
    assert out.result == "ABORTED" and out.abort_code == "REQUESTS_EXCEEDED" and out.inventory is None
    assert _physical(fake) == PREFLIGHT_PHYSICAL + 1                      # la llamada N+1 nunca se ejecutó


def test_h3_budgeted_source_refuses_double_metering(tmp_path):
    clock = Clock()
    budget = _budget(clock)
    src = CliConnectionSource(host="https://h", client_id="i", client_secret=SECRET, token_url="https://t",
                              budget=budget)
    with pytest.raises(ValueError):
        BudgetedSource(src, budget)
    BudgetedSource(SimulatedConnectionSource({}), budget)                 # sin medición propia: permitido


def test_h3_run_probe_requires_source_metered_by_same_budget(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    src, _ = _source(monkeypatch, tmp_path, clock=clock, budget=None)
    with pytest.raises(ValueError):
        _probe(src, _budget(clock), clock)


# ═══════════════════════════ H4 — duración global ═══════════════════════════
def test_h4_each_physical_call_timeout_capped_to_remaining(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock, duration=20.0)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget)
    out = _probe(src, budget, clock)
    assert out.result == "OK"
    for _, timeout, t in fake.calls:
        assert timeout == pytest.approx(min(120.0, 20.0 - t))             # nunca más allá del tiempo restante
    for timeout, t in fake.oauth_calls:
        assert timeout == pytest.approx(min(30.0, 20.0 - t))


def test_h4_slow_call_cut_by_budget_aborts_never_complete(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock, duration=30.0)
    script = lambda a: (1000.0, _proc(0, "{}")) if a[:3] == ["spaces", "connections", "get"] and "S4PREM" in a \
        and "CALIDAD" in a else None
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=script)
    out = _probe(src, budget, clock)
    assert out.result == "ABORTED" and out.abort_code == "DURATION_EXCEEDED"
    assert out.inventory is None and out.exit_code == 4
    assert clock.t <= 30.0 + 1e-9                                          # cortado EN el límite, no después


def test_h4_retry_after_beyond_remaining_aborts_without_sleeping(monkeypatch, tmp_path, real_profile):
    clock = Clock()
    budget = _budget(clock, duration=300.0)
    script = _rate_limited_then_ok(["spaces", "connections", "list", "--space", "DESARROLLO"], 1, 3600)
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget, script=script)
    out = _probe(src, budget, clock)
    assert out.result == "ABORTED" and out.abort_code == "DURATION_EXCEEDED"
    assert clock.sleeps == [] and clock.t < 300.0                         # no durmió 3600 s


def test_h4_post_run_backstop_rejects_overrun(monkeypatch, tmp_path, real_profile):
    # Una llamada que ignora su timeout (p.ej. timeout por-socket de urllib) y deja la corrida fuera de límite:
    # aunque la extracción "termine", el respaldo posterior impide presentar COMPLETE.
    clock = Clock()
    budget = _budget(clock, duration=60.0)
    last = ["spaces", "connections", "get", "--space", "CALIDAD", "--name", "SQL_SERVER_2019_onpremise"]
    src, fake = _source(monkeypatch, tmp_path, clock=clock, budget=budget)
    def script(a):
        if a == last:
            fake.ignore_timeout = True
            return 500.0, _default(a)[1]
        return None
    fake.script = script
    out = _probe(src, budget, clock)
    assert out.result == "ABORTED" and out.abort_code == "DURATION_EXCEEDED" and out.inventory is None


def test_h4_real_wallclock_bound_with_blocking_subprocess(tmp_path):
    # Reloj y proceso REALES: un CLI que no responde no puede retener la sonda más allá de la duración global.
    budget = Budget(max_requests=10, max_duration_s=1.0, max_total_retries=0)
    launcher = DSP.DatasphereCliLauncher(command=(sys.executable, "-c", "import time; time.sleep(30)"), mode="direct")
    src = CliConnectionSource(host="https://h", client_id="i", client_secret=SECRET, token_url="https://t",
                              timeout=120.0, budget=budget, work_dir=str(tmp_path))
    src._cli = DSP.DatasphereCli(host="https://h", client_id="i", client_secret=SECRET, token_url="https://t",
                                 work_dir=tmp_path, launcher=launcher, isolated_profile=True)
    budget.start()
    t0 = time.monotonic()
    with pytest.raises(BudgetError) as ei:
        src.get_connection("CALIDAD", "BW_DS")
    assert ei.value.code == "DURATION_EXCEEDED"
    assert time.monotonic() - t0 < 10.0


# ═══════════════════ B1 — salida compartible saneada ════════════════════════
from tools.catalog.connectivity_governance import (  # noqa: E402
    render_abort,
    render_review,
    render_unexpected,
    review_summary,
)
from tools.catalog.connectivity_governance.probe import ProbeOutcome  # noqa: E402

M_HOST = "https://tenant-mrkhost.devqa.invalid/"
M_TOKEN_URL = "https://auth-mrkurl.invalid/oauth/token"
M_CLIENT_ID = "sb-mrkclientid!b1"
M_SECRET = "MRK-Secret-Value-77"
M_SPACES = {"MRKSPACE_DEV": "DEV", "MRKSPACE_QAS": "QAS"}
M_PHYS_HOST = "erp.mrkphys.corp.invalid"
M_SID, M_CLIENT = "QZX", "8642"
M_AGENT = "MRKAGENT_ONPREM"
M_CONNS = {"MRKSPACE_DEV": ["MRKCONN_ERP", "MRKCONN_BW"], "MRKSPACE_QAS": ["MRKCONN_ERP", "MRKCONN_JDBC"]}
FORBIDDEN = ("mrk", "QZX", "8642", "http", "invalid", "oauth/token", "sb-")


def _marked_doc(space, name):
    if name == "MRKCONN_JDBC":       # sin atributos físicos ni agente
        return {"name": name, "space_name": space, "typeId": "GENERICJDBC", "credentialMode": "technicaluser"}
    raw = (FIXTURES / "DESARROLLO__S4ABAP.json").read_text(encoding="utf-8")
    for a, b in (("erp-a-dev.invalid", M_PHYS_HOST), ('"EAD"', f'"{M_SID}"'), ('"100"', f'"{M_CLIENT}"'),
                 ("AGENT_SHARED", M_AGENT), ("S4ABAP", name), ("DESARROLLO", space)):
        raw = raw.replace(a, b)
    return json.loads(raw)


def _echo(*extra):
    # stderr del CLI que hace eco de TODO lo sensible (peor caso)
    return " ".join([f"HTTP 403 Forbidden {M_HOST} {M_TOKEN_URL} client {M_CLIENT_ID} secret {M_SECRET}", *extra])


def _marked_script(overrides=None):
    overrides = overrides or {}

    def script(a):
        for match, resp in overrides.items():
            if tuple(a[:len(match)]) == match:
                return resp
        if a[:1] == ["login"]:
            return 0.2, _proc(0)
        if a[:2] == ["spaces", "list"]:
            return 0.1, _proc(0, json.dumps([{"name": s} for s in M_SPACES]))
        if a[:3] == ["spaces", "connections", "list"]:
            sp = a[a.index("--space") + 1]
            return 0.1, _proc(0, json.dumps([{"name": n} for n in M_CONNS.get(sp, [])]))
        if a[:3] == ["spaces", "connections", "get"]:
            sp, nm = a[a.index("--space") + 1], a[a.index("--name") + 1]
            return 0.1, _proc(0, json.dumps(_marked_doc(sp, nm)))
        return 0.1, _proc(1, stderr=_echo("unknown"))
    return script


def _marked_run(monkeypatch, tmp_path, capsys, *, overrides=None, oauth_exc=None, duration=300.0, timeout=120.0):
    clock = Clock()
    budget = _budget(clock, duration=duration)
    fake = FakeCli(clock, _marked_script(overrides))
    monkeypatch.setattr(DSP.subprocess, "run", fake)
    monkeypatch.setattr(DSP, "resolve_datasphere_cli_launcher",
                        lambda root: DSP.DatasphereCliLauncher(command=("fake-cli",), mode="direct"))
    src = CliConnectionSource(host=M_HOST, client_id=M_CLIENT_ID, client_secret=M_SECRET, token_url=M_TOKEN_URL,
                              probe_space="MRKSPACE_DEV", probe_name="MRKCONN_ERP", timeout=timeout,
                              work_dir=str(tmp_path / "wd"), budget=budget)

    def oauth(timeout=30):
        if oauth_exc is not None:
            raise oauth_exc
    src._cli_obj().oauth_preflight = oauth
    out = run_probe(src, budget, client="probe-test", tenant_id="mrktenant-id", tenant_host=M_HOST,
                    space_environment_map=M_SPACES, expected_spaces=list(M_SPACES), sleep=clock.sleep)
    src.close()
    print(render_review(out))
    shared = capsys.readouterr()          # TODO lo que llegó a stdout/stderr durante la corrida + el resumen
    return out, shared.out + shared.err


def _assert_shareable(text):
    low = text.lower()
    for marker in FORBIDDEN:
        assert marker.lower() not in low, f"filtró {marker!r}: {text}"
    assert not re.search(r"\b[0-9a-f]{8,}\b", low), "la salida no debe llevar hashes/huellas"


def test_b1_happy_path_shareable_and_informative(monkeypatch, tmp_path, capsys, real_profile):
    out, text = _marked_run(monkeypatch, tmp_path, capsys)
    _assert_shareable(text)
    s = review_summary(out)
    assert s["result"] == "OK"
    assert set(s["preflight"]) == {"oauth_token", "list_spaces", "list_connections", "read_detail"}
    assert all(v == {"status": "OK"} for v in s["preflight"].values())
    inv = s["inventory"]
    assert inv["status"] == "COMPLETE" and inv["exhaustiveness"] == "UNVERIFIABLE"
    assert inv["coverage"]["expected_spaces"] == 2 and inv["coverage"]["extracted_connections"] == 4
    refs = {c["ref"]: c for c in inv["connections"]}
    assert list(refs) == ["C1", "C2", "C3", "C4"] and {c["space_ref"] for c in refs.values()} == {"S1", "S2"}
    assert refs["C1"]["physical_present"] == {"host": True, "sysid": True, "systemnumber": True, "client": True}
    assert refs["C1"]["agent_present"]["agent_name"] is True
    assert refs["C4"]["physical_present"]["host"] is False and refs["C4"]["agent_present"]["agent_name"] is False
    # el detalle técnico sigue disponible en MEMORIA para el procesamiento (no se imprime)
    assert out.inventory["connections"][0]["physical_system"]["sysid"] == M_SID


def test_b1_permission_error_in_extraction(monkeypatch, tmp_path, capsys, real_profile):
    ov = {("spaces", "connections", "list", "--space", "MRKSPACE_QAS"): (0.1, _proc(1, stderr=_echo("MRKSPACE_QAS"))),
          ("spaces", "connections", "get", "--space", "MRKSPACE_DEV", "--name", "MRKCONN_BW"):
              (0.1, _proc(1, stderr=_echo("MRKCONN_BW", M_PHYS_HOST, M_AGENT)))}
    out, text = _marked_run(monkeypatch, tmp_path, capsys, overrides=ov)
    _assert_shareable(text)
    inv = review_summary(out)["inventory"]
    assert inv["status"] == "PARTIAL"
    assert inv["coverage"]["errors_by_code"] == {"SCOPE_DENIED": 2}
    assert inv["coverage"]["errors_by_scope"] == {"connection": 1, "space": 1}
    assert inv["coverage"]["inaccessible_spaces"] == 1


def test_b1_preflight_permission_error(monkeypatch, tmp_path, capsys, real_profile):
    ov = {("spaces", "connections", "list"): (0.1, _proc(1, stderr=_echo("MRKSPACE_DEV")))}
    out, text = _marked_run(monkeypatch, tmp_path, capsys, overrides=ov)
    _assert_shareable(text)
    pf = review_summary(out)["preflight"]
    assert out.result == "PREFLIGHT_INSUFFICIENT"
    assert pf["list_connections"] == {"status": "FAILED", "code": "SCOPE_DENIED"}
    assert pf["read_detail"] == {"status": "NOT_VERIFIED", "code": "NOT_EXECUTED"}


def test_b1_login_timeout(monkeypatch, tmp_path, capsys, real_profile):
    out, text = _marked_run(monkeypatch, tmp_path, capsys, timeout=5.0, overrides={("login",): (999.0, _proc(0))})
    _assert_shareable(text)
    assert review_summary(out)["preflight"]["oauth_token"] == {"status": "FAILED", "code": "TIMEOUT"}


def test_b1_oauth_error_with_urls_in_message(monkeypatch, tmp_path, capsys, real_profile):
    exc = DSP.DatasphereCliError(f"PRECHECK_OAUTH_NETWORK_ERROR - No se pudo contactar {M_TOKEN_URL} "
                                 f"({M_CLIENT_ID}:{M_SECRET}). Detalle: <urlopen error {M_HOST}>")
    out, text = _marked_run(monkeypatch, tmp_path, capsys, oauth_exc=exc)
    _assert_shareable(text)
    assert review_summary(out)["preflight"]["oauth_token"] == {"status": "FAILED", "code": "PRECHECK_OAUTH_NETWORK_ERROR"}


def test_b1_budget_abort(monkeypatch, tmp_path, capsys, real_profile):
    ov = {("spaces", "connections", "get", "--space", "MRKSPACE_QAS"): (1000.0, _proc(0))}
    out, text = _marked_run(monkeypatch, tmp_path, capsys, overrides=ov, duration=30.0)
    _assert_shareable(text)
    s = review_summary(out)
    assert s["result"] == "ABORTED" and s["abort"]["code"] == "DURATION_EXCEEDED" and "inventory" not in s


def test_b1_unexpected_exception_and_safety_aborts_print_codes_only():
    class MRKWeird(Exception):
        pass
    for exc in (RuntimeError(f"boom {M_HOST} {M_SECRET} {M_SID}"), OSError(f"[WinError 5] {M_PHYS_HOST}")):
        line = render_unexpected(exc)
        assert line.startswith("ABORT [UNEXPECTED]: ")
        _assert_shareable(line)
        assert "boom" not in line and "WinError" not in line
    assert render_unexpected(MRKWeird("x")) == "ABORT [UNEXPECTED]: MRKWeird"   # nombre de TIPO del código, no dato
    err = SafetyError("WORKDIR_CLEANUP_FAILED", f"no se pudo borrar C:\\Temp\\dsp-x {M_HOST}")
    assert render_abort(err.code) == "ABORT [WORKDIR_CLEANUP_FAILED]"
    assert render_abort(f"X {M_HOST}") == "ABORT [UNCLASSIFIED]"          # código fuera de vocabulario


def test_b1_unknown_codes_and_free_text_are_not_echoed():
    inv = {"status": "PARTIAL", "exhaustiveness": "INCOMPLETE",
           "coverage": {"expected_spaces": ["MRKSPACE_DEV"], "accessible_spaces": [],
                        "inaccessible_spaces": ["MRKSPACE_DEV"], "discovered_connections": 0,
                        "extracted_connections": 0,
                        "errors": [{"scope": "space:MRKSPACE_DEV", "code": f"E_{M_PHYS_HOST}", "message": M_HOST,
                                    "retriable": False}]},
           "connections": []}
    text = render_review(ProbeOutcome("OK", 0, preflight={"oauth_token": {"status": "FAILED", "detail": f"{M_HOST} zzz"}},
                                      inventory=inv))
    _assert_shareable(text)
    assert "UNCLASSIFIED" in text
