"""Salida del Datasphere CLI decodificada como UTF-8 (regresión Windows / cp1252).

El CLI (Node) emite UTF-8. Sin `encoding="utf-8"`, en Windows `subprocess.run(text=True)` decodifica con cp1252: con `Í`/`Á` el hilo lector
falla, `stdout` queda en None y `_run()` reventaba con AttributeError (visto como EXTRACTION_FAILED en Contreras · AM_Turnover); con `é`/`ñ`
el texto se corrompía en silencio. Estos tests ejecutan un proceso REAL (el intérprete actual) que escribe bytes UTF-8, por la ruta
principal y por el fallback del launcher, sin red ni Datasphere."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tools.catalog import DSP_CLI_Export_Lineage as dsp

LABEL = "Índice Área · niño · Género"
READ = ["objects", "views", "read", "--space", "DEV", "--technical-name", "3VF_X"]


def _script(tmp_path, payload: bytes):
    path = tmp_path / "fake_cli.py"
    path.write_text(f"import sys\nsys.stdout.buffer.write({payload!r})\nsys.stdout.buffer.flush()\n", encoding="utf-8")
    return path


def _cli(tmp_path, command, fallback=None):
    launcher = dsp.DatasphereCliLauncher(command=tuple(command), mode="direct",
                                         fallback_command=tuple(fallback) if fallback else None)
    return dsp.DatasphereCli(host="https://example.test", client_id="client", client_secret="super-secret",
                             token_url="https://auth.test/oauth/token", work_dir=tmp_path, launcher=launcher)


def _doc():
    return json.dumps({"definitions": {"3VF_X": {"@EndUserText.label": LABEL}}}, ensure_ascii=False).encode("utf-8")


def test_main_launcher_preserves_utf8_content_exactly(tmp_path):
    cli = _cli(tmp_path, [sys.executable, str(_script(tmp_path, _doc()))])
    data = cli._run(READ, expect_json=True)
    assert data["definitions"]["3VF_X"]["@EndUserText.label"] == LABEL          # sin excepción y sin «GÃ©nero»


def test_fallback_launcher_decodes_the_same_way(tmp_path):
    cli = _cli(tmp_path, ["no-such-datasphere-binary-xyz"], fallback=[sys.executable, str(_script(tmp_path, _doc()))])
    data = cli._run(READ, expect_json=True)
    assert data["definitions"]["3VF_X"]["@EndUserText.label"] == LABEL


def test_invalid_utf8_is_never_silently_replaced(tmp_path):
    """Decodificación ESTRICTA: bytes inválidos no se convierten en «�»; la lectura falla de forma explícita y clasificada."""
    cli = _cli(tmp_path, [sys.executable, str(_script(tmp_path, b'{"label": "\xff\xfe"}'))])
    with pytest.raises(dsp.DatasphereCliError, match="CLI_OUTPUT_UNAVAILABLE"):
        cli._run(READ, expect_json=True)


@pytest.mark.parametrize("stdout, match", [
    (None, "CLI_OUTPUT_UNAVAILABLE"),                  # salida no legible ≠ salida vacía
    ("   ", "Command returned empty output"),
    ("{not json", "Expected JSON but could not parse"),
])
def test_output_states_are_distinguished(tmp_path, monkeypatch, stdout, match):
    cli = _cli(tmp_path, ["unused"])
    monkeypatch.setattr(cli, "_invoke_cli", lambda args, timeout=None: (subprocess.CompletedProcess(["datasphere", *args], 0, stdout=stdout, stderr=""), ["datasphere", *args]))
    with pytest.raises(dsp.DatasphereCliError, match=match):
        cli._run(READ, expect_json=True)


def test_failed_command_without_readable_output_reports_the_exit_code(tmp_path, monkeypatch):
    cli = _cli(tmp_path, ["unused"])
    monkeypatch.setattr(cli, "_invoke_cli", lambda args, timeout=None: (subprocess.CompletedProcess(["datasphere", *args], 3, stdout=None, stderr=None), ["datasphere", *args]))
    with pytest.raises(dsp.DatasphereCliError, match="exit code 3"):
        cli._run(READ, expect_json=True)


def test_safe_read_records_the_failure_instead_of_aborting_the_walk(tmp_path, monkeypatch):
    """Una lectura ilegible ya no es una excepción genérica que corta la extracción: `safe_read` la registra como READ_FAILED."""
    cli = _cli(tmp_path, ["unused"])
    monkeypatch.setattr(cli, "_invoke_cli", lambda args, timeout=None: (subprocess.CompletedProcess(["datasphere", *args], 0, stdout=None, stderr=""), ["datasphere", *args]))
    exporter = dsp.LineageExporter.__new__(dsp.LineageExporter)
    exporter.cli, exporter.space, exporter.read_cache, exporter.failures = cli, "DEV", {}, []
    exporter.diagnostics = cli.diagnostics
    assert exporter.safe_read("views", "3VF_X") is None
    assert exporter.failures == [{"kind": "read", "object_type": "views", "technical_name": "3VF_X", "code": "READ_FAILED"}]
