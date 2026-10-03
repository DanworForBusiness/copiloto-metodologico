from __future__ import annotations

import json
import subprocess

import pytest

from tools.catalog import DSP_CLI_Export_Lineage as dsp


def launcher(command=("node", "/repo/node_modules/@sap/datasphere-cli/terminal.js"), *, mode="direct", fallback=("npx", "--no-install", "datasphere")):
    return dsp.DatasphereCliLauncher(
        command=tuple(command),
        mode=mode,
        fallback_command=tuple(fallback) if fallback is not None else None,
    )


def make_cli(tmp_path, *, selected=None):
    return dsp.DatasphereCli(
        host="https://example.test",
        client_id="client",
        client_secret="super-secret",
        token_url="https://auth.test/oauth/token",
        work_dir=tmp_path,
        launcher=selected or launcher(),
    )


def completed(cmd, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)


def test_resolver_prefers_local_entrypoint(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    entry = root / "node_modules" / "@sap" / "datasphere-cli" / "terminal.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// cli", encoding="utf-8")

    def fake_which(name):
        return {"node": "/runtime/node", "npx": "/runtime/npx"}.get(name)

    monkeypatch.setattr(dsp.shutil, "which", fake_which)
    selected = dsp.resolve_datasphere_cli_launcher(root)

    assert selected.mode == "direct"
    assert selected.command == ("/runtime/node", str(entry))
    assert selected.fallback_command == ("npx", "--no-install", "datasphere")


def test_resolver_uses_npx_when_local_entrypoint_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dsp.shutil,
        "which",
        lambda name: {"node": "/runtime/node", "npx": "/runtime/npx"}.get(name),
    )
    selected = dsp.resolve_datasphere_cli_launcher(tmp_path / "repo")
    assert selected.mode == "npx"
    assert selected.command == ("npx", "--no-install", "datasphere")
    assert selected.fallback_command is None


def test_resolver_fails_when_no_launcher_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(dsp.shutil, "which", lambda name: None)
    with pytest.raises(dsp.DatasphereCliError, match="DATASPHERE_CLI_UNAVAILABLE"):
        dsp.resolve_datasphere_cli_launcher(tmp_path / "repo")


def test_direct_receives_exact_args_and_preserves_cwd_env_home(monkeypatch, tmp_path):
    cli = make_cli(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return completed(cmd, stdout="[]")

    monkeypatch.setattr(dsp.subprocess, "run", fake_run)
    args = ["objects", "views", "list", "--space", "DSDEV", "--top", "200", "--skip", "0"]

    assert cli._run(args, expect_json=True) == []
    assert calls[0][0] == [*cli.launcher.command, *args]
    assert calls[0][1]["cwd"] == cli.project_root
    assert calls[0][1]["env"]["HOME"] == str(cli.home_dir)
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True


def test_direct_start_failure_falls_back_once(monkeypatch, tmp_path):
    cli = make_cli(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "node":
            raise FileNotFoundError("node disappeared")
        return completed(cmd, stdout="[]")

    monkeypatch.setattr(dsp.subprocess, "run", fake_run)
    assert cli._run(["objects", "views", "list"], expect_json=True) == []
    assert calls == [
        ["node", "/repo/node_modules/@sap/datasphere-cli/terminal.js", "objects", "views", "list"],
        ["npx", "--no-install", "datasphere", "objects", "views", "list"],
    ]


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("OAuth failed", "OAuth failed"),
        ("Authorization denied", "Authorization denied"),
        ("ROOT_OBJECT_NOT_FOUND", "ROOT_OBJECT_NOT_FOUND"),
    ],
)
def test_functional_nonzero_never_retries_through_npx(monkeypatch, tmp_path, stderr, expected):
    cli = make_cli(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return completed(cmd, rc=1, stderr=stderr)

    monkeypatch.setattr(dsp.subprocess, "run", fake_run)
    with pytest.raises(dsp.DatasphereCliError, match=expected):
        cli._run(["objects", "views", "list"], expect_json=True)
    assert len(calls) == 1
    assert calls[0][0] == "node"


def test_stdout_stderr_and_exit_code_contract(monkeypatch, tmp_path):
    cli = make_cli(tmp_path)

    monkeypatch.setattr(
        dsp.subprocess,
        "run",
        lambda cmd, **kwargs: completed(cmd, stdout="plain-output\n", stderr="warning\n"),
    )
    assert cli._run(["login"], expect_json=False) == "plain-output\n"

    monkeypatch.setattr(
        dsp.subprocess,
        "run",
        lambda cmd, **kwargs: completed(cmd, rc=7, stdout="out", stderr="err-seven"),
    )
    with pytest.raises(dsp.DatasphereCliError, match="err-seven"):
        cli._run(["objects", "views", "list"], expect_json=True)


def test_malformed_json_is_preserved_as_functional_error_without_retry(monkeypatch, tmp_path):
    cli = make_cli(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return completed(cmd, stdout="{not-json")

    monkeypatch.setattr(dsp.subprocess, "run", fake_run)
    with pytest.raises(dsp.DatasphereCliError, match="Expected JSON but could not parse CLI output"):
        cli._run(["objects", "views", "list"], expect_json=True)
    assert len(calls) == 1


def test_capture_output_and_secret_redaction_contract(monkeypatch, tmp_path):
    cli = make_cli(tmp_path)
    lines = []
    monkeypatch.setattr(
        dsp.subprocess,
        "run",
        lambda cmd, **kwargs: completed(cmd, stdout="ok"),
    )
    with dsp.capture_output(on_line=lines.append):
        cli.login()
    assert "super-secret" not in "\n".join(lines)


class FakeRunExportCli:
    last_launcher = None

    def __init__(self, *, launcher=None, **kwargs):
        type(self).last_launcher = launcher
        self.diagnostics = kwargs.get("diagnostics") or dsp.ExtractionDiagnostics(False)

    def oauth_preflight(self):
        return None

    def login(self):
        return None

    def authorization_preflight(self, space):
        return None

    def list_objects(self, space, object_type):
        if object_type == "analytic-models":
            return [{"technicalName": "ROOT"}]
        return []

    def read_object(self, space, object_type, technical_name):
        return {}


def test_run_export_manifest_and_orchestration_contract(monkeypatch, tmp_path):
    selected = launcher()
    monkeypatch.setattr(dsp, "resolve_datasphere_cli_launcher", lambda project_root: selected)
    monkeypatch.setattr(dsp, "DatasphereCli", FakeRunExportCli)

    catalog_root = tmp_path / "catalog"
    cache_root = tmp_path / "cache"
    req = dsp.ExportRequest(
        host="https://example.test",
        client_id="client",
        client_secret="secret",
        token_url="https://auth.test/oauth/token",
        space="DSDEV",
        client="Pampa",
        root_object="ROOT",
        root_type="analytic-model",
        allowed_root_types={"analytic-model"},
        features_requested={"analytic-models"},
        catalog_root=catalog_root,
        csn_cache_dir=cache_root,
        generate_catalog=False,
        mirror_up=False,
    )
    result = dsp.run_export(req)

    assert result.ok is True
    assert result.root_object == "ROOT"
    assert result.root_type == "analytic-model"
    assert result.pool_dir == cache_root / "Pampa"
    assert result.manifest_path is not None and result.manifest_path.exists()
    assert FakeRunExportCli.last_launcher is selected

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["rootObject"] == "ROOT"
    assert manifest["rootType"] == "analytic-model"


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("PRECHECK_OAUTH_401 - bad credentials", "AUTHENTICATION_FAILED"),
        ("PRECHECK_AUTH_403 - denied", "INSUFFICIENT_DATASPHERE_PRIVILEGES"),
    ],
)
def test_run_export_preserves_precheck_classification(monkeypatch, tmp_path, failure, expected_code):
    selected = launcher()
    monkeypatch.setattr(dsp, "resolve_datasphere_cli_launcher", lambda project_root: selected)

    class FailingCli(FakeRunExportCli):
        def oauth_preflight(self):
            if failure.startswith("PRECHECK_OAUTH"):
                raise dsp.DatasphereCliError(failure)

        def authorization_preflight(self, space):
            if failure.startswith("PRECHECK_AUTH"):
                raise dsp.DatasphereCliError(failure)

    monkeypatch.setattr(dsp, "DatasphereCli", FailingCli)
    result = dsp.run_export(
        dsp.ExportRequest(
            host="h",
            client_id="id",
            client_secret="secret",
            token_url="t",
            space="DSDEV",
            client="Pampa",
            root_object="ROOT",
            catalog_root=tmp_path / "catalog",
            csn_cache_dir=tmp_path / "cache",
            generate_catalog=False,
            mirror_up=False,
        )
    )
    assert result.ok is False
    assert result.error_code == expected_code


def test_run_export_launcher_resolution_failure_is_structured(monkeypatch, tmp_path):
    def fail(project_root):
        raise dsp.DatasphereCliError("DATASPHERE_CLI_UNAVAILABLE - missing")

    monkeypatch.setattr(dsp, "resolve_datasphere_cli_launcher", fail)
    result = dsp.run_export(
        dsp.ExportRequest(
            host="h",
            client_id="id",
            client_secret="secret",
            token_url="t",
            space="DSDEV",
            client="Pampa",
            root_object="ROOT",
            catalog_root=tmp_path / "catalog",
            csn_cache_dir=tmp_path / "cache",
            generate_catalog=False,
            mirror_up=False,
        )
    )
    assert result.ok is False
    assert result.error_code == "DATASPHERE_CLI_UNAVAILABLE"
    assert result.exit_code == 2
    assert "secret" not in result.message
