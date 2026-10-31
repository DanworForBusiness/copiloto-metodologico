"""F2.1b — hardening del adaptador CLI REAL (CliConnectionSource), con subprocess y respuestas SIMULADAS.

Ejercita el camino real (CliConnectionSource → DatasphereCli._run → subprocess) monkeypatcheando
`subprocess.run` para simular salidas/errores del CLI, SIN conectarse a ningún tenant. Cubre: timeout
controlado y estructurado, clasificación verificable de errores (429/Retry-After, 401, 403, timeout, JSON
inválido, error de ejecución), no-reintento de autorización, self-retry de list, comandos exactos, y el
preflight de permisos separado (OK/FAILED/NOT_VERIFIED).
"""
from __future__ import annotations

import subprocess
import types

import pytest

import tools.catalog.DSP_CLI_Export_Lineage as DSP
from tools.catalog.connectivity_governance import (
    CheckStatus,
    CliConnectionSource,
    RetryPolicy,
    SourceError,
    classify_cli_error,
    with_retry,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _proc(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Reemplaza subprocess.run; `handler(ds_args, kwargs)` devuelve un proc o lanza. Registra kwargs
    (para verificar que se pasa `timeout`) y los argv datasphere (cmd sin el launcher)."""
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, cmd, **kwargs):
        ds_args = list(cmd[1:])            # cmd = [launcher, *ds_args]
        self.calls.append({"ds_args": ds_args, "timeout": kwargs.get("timeout")})
        return self.handler(ds_args, kwargs)


def _make_source(monkeypatch, tmp_path, handler, **kw):
    fake = FakeRun(handler)
    monkeypatch.setattr(DSP.subprocess, "run", fake)
    launcher = DSP.DatasphereCliLauncher(command=("fake-cli",), mode="direct")
    cli = DSP.DatasphereCli(host="https://h", client_id="id", client_secret="SECRET",
                            token_url="https://t/oauth/token", work_dir=tmp_path, launcher=launcher)
    src = CliConnectionSource(host="https://h", client_id="id", client_secret="SECRET",
                              token_url="https://t/oauth/token", **kw)
    src._cli = cli
    return src, fake


# ── clasificación (unit, determinista) ────────────────────────────────────────
def test_classify_covers_each_category():
    assert classify_cli_error(subprocess.TimeoutExpired("c", 1)).code == "TIMEOUT"
    assert classify_cli_error(subprocess.TimeoutExpired("c", 1)).retriable is True
    r429 = classify_cli_error(Exception("HTTP 429 Too Many Requests; Retry-After: 3"))
    assert r429.code == "RATE_LIMITED" and r429.retriable is True and r429.retry_after == 3.0
    assert classify_cli_error(Exception("HTTP 403 Forbidden")).code == "SCOPE_DENIED"
    assert classify_cli_error(Exception("HTTP 403 Forbidden")).retriable is False      # autorización: NO reintentar
    assert classify_cli_error(Exception("401 Unauthorized")).code == "AUTH_401"
    assert classify_cli_error(Exception("401 Unauthorized")).retriable is False
    assert classify_cli_error(Exception("Expected JSON but could not parse")).code == "INVALID_OR_INCOMPLETE_JSON"
    assert classify_cli_error(Exception("Command returned empty output")).code == "INVALID_OR_INCOMPLETE_JSON"
    generic = classify_cli_error(Exception("boom unexpected"))
    assert generic.code == "CLI_EXECUTION_ERROR" and generic.retriable is False        # no asumir transitorio


def test_classify_sanitizes_secret():
    err = classify_cli_error(Exception("login failed --client-secret TOPSECRETXYZ"))
    assert "TOPSECRETXYZ" not in err.message and "[REDACTED]" in err.message


# ── timeout controlado + estructurado (plumbing real) ─────────────────────────
def test_timeout_is_passed_and_mapped(monkeypatch, tmp_path):
    def handler(ds_args, kwargs):
        raise subprocess.TimeoutExpired(cmd="fake", timeout=kwargs.get("timeout"))
    src, fake = _make_source(monkeypatch, tmp_path, handler, timeout=30.0)
    with pytest.raises(SourceError) as ei:
        src.get_connection("CALIDAD", "BW_DS")
    assert ei.value.code == "TIMEOUT" and ei.value.retriable is True
    assert fake.calls[-1]["timeout"] == 30.0          # el timeout llegó a subprocess.run


# ── clasificación por el camino real (subprocess returncode/stderr) ───────────
def test_permission_error_via_subprocess_is_scope_denied_and_not_retried(monkeypatch, tmp_path):
    calls = {"n": 0}
    def handler(ds_args, kwargs):
        calls["n"] += 1
        return _proc(returncode=1, stderr="HTTP 403 Forbidden: insufficient privileges")
    src, fake = _make_source(monkeypatch, tmp_path, handler, timeout=10.0)
    # get_connection clasifica; el orquestador lo reintentaría SOLO si fuera retriable → acá NO.
    with pytest.raises(SourceError) as ei:
        with_retry(lambda: src.get_connection("CALIDAD", "BW_DS"), RetryPolicy(max_attempts=4, sleep=lambda s: None))
    assert ei.value.code == "SCOPE_DENIED"
    assert calls["n"] == 1                              # autorización NO se reintenta


def test_empty_output_is_invalid_or_incomplete(monkeypatch, tmp_path):
    src, fake = _make_source(monkeypatch, tmp_path, lambda a, k: _proc(returncode=0, stdout="   "))
    with pytest.raises(SourceError) as ei:
        src.get_connection("CALIDAD", "BW_DS")
    assert ei.value.code == "INVALID_OR_INCOMPLETE_JSON" and ei.value.retriable is False


def test_bad_json_is_invalid_or_incomplete(monkeypatch, tmp_path):
    src, fake = _make_source(monkeypatch, tmp_path, lambda a, k: _proc(returncode=0, stdout="{not json"))
    with pytest.raises(SourceError) as ei:
        src.get_connection("CALIDAD", "BW_DS")
    assert ei.value.code == "INVALID_OR_INCOMPLETE_JSON"


# ── self-retry de list_connections ante 429 (la fuente dueña de su reintento) ──
def test_list_connections_retries_rate_limit_then_succeeds(monkeypatch, tmp_path):
    slept = []
    state = {"n": 0}
    def handler(ds_args, kwargs):
        if ds_args[:3] == ["spaces", "connections", "list"]:
            state["n"] += 1
            if state["n"] == 1:
                return _proc(returncode=1, stderr="429 Too Many Requests; Retry-After: 2")
            return _proc(returncode=0, stdout='[{"name": "BW_DS"}]')
        return _proc(returncode=0, stdout="[]")
    src, fake = _make_source(monkeypatch, tmp_path, handler, timeout=10.0,
                             retry=RetryPolicy(max_attempts=3, sleep=lambda s: slept.append(s)))
    out = src.list_connections("CALIDAD")
    assert out == [{"name": "BW_DS"}]
    assert slept == [2.0]                               # respetó Retry-After
    assert state["n"] == 2


# ── comandos EXACTOS que emite el adaptador ───────────────────────────────────
def test_exact_commands(monkeypatch, tmp_path):
    def handler(ds_args, kwargs):
        if ds_args[:2] == ["spaces", "list"]:
            return _proc(returncode=0, stdout='[{"name": "DESARROLLO"}]')
        if ds_args[:3] == ["spaces", "connections", "list"]:
            return _proc(returncode=0, stdout='[{"name": "BW_DS"}]')
        if ds_args[:3] == ["spaces", "connections", "get"]:
            return _proc(returncode=0, stdout='{"name": "BW_DS"}')
        return _proc(returncode=0, stdout="[]")
    src, fake = _make_source(monkeypatch, tmp_path, handler, timeout=10.0)
    src.list_spaces(); src.list_connections("DESARROLLO"); src.get_connection("DESARROLLO", "BW_DS")
    emitted = [c["ds_args"] for c in fake.calls]
    assert ["spaces", "list"] in emitted
    assert ["spaces", "connections", "list", "--space", "DESARROLLO"] in emitted
    assert ["spaces", "connections", "get", "--space", "DESARROLLO", "--name", "BW_DS"] in emitted


# ── preflight de permisos separado (OK / FAILED / NOT_VERIFIED) ────────────────
def _preflight_source(monkeypatch, tmp_path, *, oauth_ok=True, handler=None, **kw):
    handler = handler or (lambda a, k: _proc(returncode=0, stdout='[{"name": "X"}]'))
    src, fake = _make_source(monkeypatch, tmp_path, handler, timeout=10.0, **kw)
    src._cli.oauth_preflight = (lambda: None) if oauth_ok else (_raise_oauth)
    src._cli.login = lambda *a, **k: None
    return src, fake


def _raise_oauth():
    raise DSP.DatasphereCliError("PRECHECK_OAUTH_401 - rechazó credenciales")


def test_preflight_all_ok(monkeypatch, tmp_path):
    src, _ = _preflight_source(monkeypatch, tmp_path, probe_space="CALIDAD", probe_name="BW_DS")
    rep = src.preflight_report()
    assert rep.status_of("oauth_token") == CheckStatus.OK
    assert rep.status_of("list_spaces") == CheckStatus.OK
    assert rep.status_of("list_connections") == CheckStatus.OK
    assert rep.status_of("read_detail") == CheckStatus.OK
    assert rep.authorized_for_extraction() is True


def test_preflight_oauth_fail_marks_rest_not_verified(monkeypatch, tmp_path):
    src, _ = _preflight_source(monkeypatch, tmp_path, oauth_ok=False, probe_space="CALIDAD", probe_name="BW_DS")
    rep = src.preflight_report()
    assert rep.status_of("oauth_token") == CheckStatus.FAILED
    for n in ("list_spaces", "list_connections", "read_detail"):
        assert rep.status_of(n) == CheckStatus.NOT_VERIFIED    # NO ejecutada ⇒ nunca "autorizada"
    assert rep.authorized_for_extraction() is False


def test_preflight_detail_not_verified_without_probe_name(monkeypatch, tmp_path):
    src, _ = _preflight_source(monkeypatch, tmp_path, probe_space="CALIDAD")  # sin probe_name
    rep = src.preflight_report()
    assert rep.status_of("list_connections") == CheckStatus.OK
    assert rep.status_of("read_detail") == CheckStatus.NOT_VERIFIED


def test_preflight_raises_when_oauth_fails(monkeypatch, tmp_path):
    src, _ = _preflight_source(monkeypatch, tmp_path, oauth_ok=False, probe_space="CALIDAD")
    with pytest.raises(SourceError) as ei:
        src.preflight()
    assert ei.value.code == "OAUTH_FAILED"


# ── exhaustividad declarada, no comprobada ────────────────────────────────────
def test_exhaustiveness_is_declared_not_guaranteed(monkeypatch, tmp_path):
    src, _ = _make_source(monkeypatch, tmp_path, lambda a, k: _proc(returncode=0, stdout="[]"), timeout=10.0)
    assert src.guarantees_exhaustive_list is False
