"""
test_config_audit.py — Config Governance audit tool (`tools/config_audit.py`).

`tools/` isn't an importable package in this repo (no `__init__.py`,
scripts run standalone via `python tools/...`) — loaded here via
`importlib.util.spec_from_file_location`, same idiom any standalone script
needs for test coverage without turning it into a package import surface.

Covers: AST-based variable discovery (never a hand-duplicated list),
classification (metadata map wins, otherwise inferred from the variable's
own code default), status computation for every classification x presence
combination (the two explicit examples from the ticket: Spaces absent
locally must never fail; AZURE_CLIENT_ID/TENANT_ID must be flagged
REQUIRED_LOCAL/Graph even though they always resolve via a safe default),
DEPRECATED/UNUSED drift detection, secret-value non-disclosure, and that
the actual `.env.example` this PR ships never drifts from `config.py`."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOOL_PATH = _ROOT / "tools" / "config_audit.py"
_REAL_ENV_EXAMPLE = _ROOT / ".env.example"
_REAL_CONFIG_PY = _ROOT / "config.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("config_audit", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ca = _load_module()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


_FAKE_CONFIG_SRC = '''
import os

def _env(name, default):
    return os.getenv(name, default).strip() or default

def _csv_env(name, default):
    raw = _env(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]

FOO = _env("FOO", "bar")
BAZ = _csv_env("BAZ", "a,b")
QUX = os.getenv("QUX", "")
EMPTY_DEFAULT = _env("EMPTY_DEFAULT", "")

def _resolve(dynamic_name):
    # Non-literal first arg -- must be skipped, it doesn't declare a NEW
    # variable name (mirrors _env's own `os.getenv(name, default)` body).
    return _env(dynamic_name, "irrelevant")
'''


# ============================================================================
# 1. Discovery — static AST scan, never a hand-duplicated list
# ============================================================================

def test_discover_finds_env_and_csv_env_and_getenv_calls(tmp_path):
    fake = _write(tmp_path / "fake_config.py", _FAKE_CONFIG_SRC)
    found = ca.discover_config_vars(fake)
    assert found["FOO"] == "bar"
    assert found["BAZ"] == "a,b"
    assert found["QUX"] == ""
    assert found["EMPTY_DEFAULT"] == ""


def test_discover_skips_calls_with_non_literal_first_argument(tmp_path):
    fake = _write(tmp_path / "fake_config.py", _FAKE_CONFIG_SRC)
    found = ca.discover_config_vars(fake)
    assert "dynamic_name" not in found
    assert "irrelevant" not in found
    assert len(found) == 4  # FOO, BAZ, QUX, EMPTY_DEFAULT -- nothing else


def test_discover_against_real_config_py_finds_known_vars():
    found = ca.discover_config_vars(_REAL_CONFIG_PY)
    assert found["SPACES_ENDPOINT"] == ""
    assert found["AZURE_CLIENT_ID"]  # real, non-empty default
    assert found["HC_CATALOG_ITEM_ID"] == ""
    assert found["SESSION_SECRET"]  # real, non-empty (insecure-dev) default


# ============================================================================
# 2. Classification — metadata map wins, otherwise inferred from the default
# ============================================================================

def test_classify_metadata_override_wins(monkeypatch):
    monkeypatch.setitem(ca.VAR_METADATA, "SOME_VAR", ("Human Capital", ca.CAPABILITY_OPTIONAL))
    assert ca.classify("SOME_VAR", "has-a-default-anyway") == ("Human Capital", ca.CAPABILITY_OPTIONAL)


def test_classify_infers_required_local_from_nonempty_default():
    assert ca.classify("NOT_IN_METADATA", "some-real-default") == ("Core Hub", ca.REQUIRED_LOCAL)


def test_classify_infers_capability_optional_from_empty_default():
    assert ca.classify("NOT_IN_METADATA_EITHER", "") == ("Core Hub", ca.CAPABILITY_OPTIONAL)
    assert ca.classify("NOT_IN_METADATA_EITHER", None) == ("Core Hub", ca.CAPABILITY_OPTIONAL)


# ============================================================================
# 3. Status computation — every classification x presence combination
# ============================================================================

@pytest.mark.parametrize("classification", [
    ca.REQUIRED_LOCAL, ca.REQUIRED_DEPLOYMENT, ca.CAPABILITY_OPTIONAL, ca.INTENTIONALLY_UNAVAILABLE_LOCAL,
])
def test_compute_status_present_regardless_of_classification(classification):
    assert ca.compute_status(effectively_present=True, classification=classification) == ca.STATUS_PRESENT


def test_compute_status_intentionally_unavailable():
    assert ca.compute_status(
        effectively_present=False, classification=ca.INTENTIONALLY_UNAVAILABLE_LOCAL,
    ) == ca.STATUS_INTENTIONALLY_UNAVAILABLE


def test_compute_status_required_local_absent_is_required_missing():
    assert ca.compute_status(
        effectively_present=False, classification=ca.REQUIRED_LOCAL,
    ) == ca.STATUS_REQUIRED_MISSING


def test_compute_status_capability_optional_absent_is_optional_missing():
    assert ca.compute_status(
        effectively_present=False, classification=ca.CAPABILITY_OPTIONAL,
    ) == ca.STATUS_OPTIONAL_MISSING


def test_compute_status_required_deployment_absent_locally_is_non_fatal():
    # The exact nuance requested: a REQUIRED_DEPLOYMENT variable absent from
    # a LOCAL .env is not a local problem -- must never be REQUIRED_MISSING.
    assert ca.compute_status(
        effectively_present=False, classification=ca.REQUIRED_DEPLOYMENT,
    ) == ca.STATUS_OPTIONAL_MISSING


# ============================================================================
# 4. Full audit() — drift detection, exit_ok, the two mandatory examples
# ============================================================================

def test_audit_detects_deprecated_var_present_in_env_but_unread_by_code(tmp_path):
    config_path = _write(tmp_path / "config.py", 'FOO = _env("FOO", "bar")\ndef _env(n, d): return d\n')
    env_path = _write(tmp_path / ".env", "FOO=x\nSTALE_VAR_NOBODY_READS=leftover\n")
    example_path = _write(tmp_path / ".env.example", "FOO=bar\n")
    report = ca.audit(config_path=config_path, env_path=env_path, example_path=example_path)
    assert "STALE_VAR_NOBODY_READS" in report["deprecated_in_env"]
    assert report["exit_ok"] is False


def test_audit_detects_unused_var_documented_but_unread_by_code(tmp_path):
    config_path = _write(tmp_path / "config.py", 'FOO = _env("FOO", "bar")\ndef _env(n, d): return d\n')
    env_path = _write(tmp_path / ".env", "FOO=x\n")
    example_path = _write(tmp_path / ".env.example", "FOO=bar\nGHOST_VAR_NEVER_READ=\n")
    report = ca.audit(config_path=config_path, env_path=env_path, example_path=example_path)
    assert "GHOST_VAR_NEVER_READ" in report["unused_in_example"]
    assert report["exit_ok"] is False


def test_audit_required_missing_fails(tmp_path, monkeypatch):
    monkeypatch.setitem(ca.VAR_METADATA, "MUST_HAVE_IT", ("Core Hub", ca.REQUIRED_LOCAL))
    config_path = _write(tmp_path / "config.py", 'MUST_HAVE_IT = _env("MUST_HAVE_IT", "")\ndef _env(n, d): return d\n')
    env_path = _write(tmp_path / ".env", "")
    example_path = _write(tmp_path / ".env.example", "MUST_HAVE_IT=\n")
    report = ca.audit(config_path=config_path, env_path=env_path, example_path=example_path)
    assert "MUST_HAVE_IT" in report["required_missing"]
    assert report["exit_ok"] is False


def test_audit_clean_run_is_exit_ok(tmp_path):
    config_path = _write(tmp_path / "config.py", 'FOO = _env("FOO", "bar")\ndef _env(n, d): return d\n')
    env_path = _write(tmp_path / ".env", "FOO=x\n")
    example_path = _write(tmp_path / ".env.example", "FOO=bar\n")
    report = ca.audit(config_path=config_path, env_path=env_path, example_path=example_path)
    assert report["exit_ok"] is True
    assert report["required_missing"] == []
    assert report["deprecated_in_env"] == []
    assert report["unused_in_example"] == []


def test_spaces_credentials_absent_locally_never_fail_preflight():
    # Mandatory example from the ticket: SPACES_ENDPOINT/BUCKET/KEY/SECRET
    # absent in a local .env must be INTENTIONALLY_UNAVAILABLE, never an
    # exit-code failure by themselves.
    report = ca.audit(env_path=Path("__definitely_does_not_exist__.env"))
    spaces_vars = {v["name"]: v for v in report["variables"] if v["capability"] == "Spaces"}
    for name in ("SPACES_ENDPOINT", "SPACES_BUCKET", "SPACES_KEY", "SPACES_SECRET"):
        assert spaces_vars[name]["status"] == ca.STATUS_INTENTIONALLY_UNAVAILABLE, name
    assert report["required_missing"] == []


def test_azure_graph_vars_are_flagged_required_local_and_present():
    # Mandatory example from the ticket: if the user wants local login/Graph,
    # AZURE_CLIENT_ID/TENANT_ID must show up as relevant required config --
    # they resolve PRESENT via a real code default, but under the Graph
    # capability with REQUIRED_LOCAL classification, not lumped in as some
    # anonymous optional cosmetic default.
    report = ca.audit(env_path=Path("__definitely_does_not_exist__.env"))
    by_name = {v["name"]: v for v in report["variables"]}
    for name in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID"):
        assert by_name[name]["capability"] == "Graph"
        assert by_name[name]["classification"] == ca.REQUIRED_LOCAL
        assert by_name[name]["status"] == ca.STATUS_PRESENT


def test_missing_env_file_is_treated_as_empty_not_an_error():
    report = ca.audit(env_path=Path("__definitely_does_not_exist__.env"))
    assert report["env_file_existed"] is False
    assert isinstance(report["variables"], list) and report["variables"]


# ============================================================================
# 5. Secret values are never disclosed anywhere in the report or its renders
# ============================================================================

def test_secret_value_never_appears_in_json_or_text_output(tmp_path):
    secret_value = "SUPER-SECRET-VALUE-MUST-NEVER-APPEAR-ANYWHERE"
    config_path = _write(
        tmp_path / "config.py",
        'SESSION_SECRET = _env("SESSION_SECRET", "dev-default")\ndef _env(n, d): return d\n',
    )
    env_path = _write(tmp_path / ".env", f"SESSION_SECRET={secret_value}\n")
    example_path = _write(tmp_path / ".env.example", "SESSION_SECRET=dev-default\n")
    report = ca.audit(config_path=config_path, env_path=env_path, example_path=example_path)
    dumped = json.dumps(report)
    rendered = ca._format_text(report)
    assert secret_value not in dumped
    assert secret_value not in rendered
    # The report legitimately marks it PRESENT (env override) -- that's a
    # status, never the value.
    by_name = {v["name"]: v for v in report["variables"]}
    assert by_name["SESSION_SECRET"]["status"] == ca.STATUS_PRESENT
    assert by_name["SESSION_SECRET"]["in_env_file"] is True


# ============================================================================
# 6. This PR's actual .env.example must never drift from the actual config.py
# ============================================================================

def test_shipped_env_example_has_zero_drift_against_real_config_py():
    discovered = set(ca.discover_config_vars(_REAL_CONFIG_PY))
    example_keys, existed = ca.read_env_keys(_REAL_ENV_EXAMPLE)
    assert existed, ".env.example must exist at the repo root"
    assert example_keys - discovered == set(), "documented but not read by config.py (UNUSED)"
    assert discovered - example_keys == set(), "read by config.py but undocumented in .env.example"


def test_real_env_example_produces_zero_required_missing_and_zero_drift():
    report = ca.audit(env_path=_REAL_ENV_EXAMPLE, example_path=_REAL_ENV_EXAMPLE)
    assert report["required_missing"] == []
    assert report["exit_ok"] is True


# ============================================================================
# 7. Text output — grouped by capability
# ============================================================================

def test_text_output_groups_by_all_six_capability_headers():
    report = ca.audit(env_path=Path("__definitely_does_not_exist__.env"))
    rendered = ca._format_text(report)
    for capability in ca.CAPABILITIES:
        assert f"== {capability} ==" in rendered


# ============================================================================
# 8. CLI entry point
# ============================================================================

def test_main_returns_zero_on_clean_env_file(tmp_path, capsys, monkeypatch):
    env_path = _write(tmp_path / ".env", "")
    example_path = _write(tmp_path / ".env.example", "")
    monkeypatch.setattr(ca, "_ENV_EXAMPLE", example_path)
    code = ca.main(["--env-file", str(env_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "VERDICT: OK" in out


def test_main_json_format_is_valid_json(tmp_path, capsys):
    env_path = _write(tmp_path / ".env", "")
    code = ca.main(["--env-file", str(env_path), "--format", "json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)  # must not raise
    assert "variables" in parsed
