"""test_operations_timenotes_js.py — Hotfix de consistencia de notificaciones de Carga de Horas (Operaciones).

Corre el harness `operations_timenotes.node.js` (DOM mínimo + seam `window.__opsTimenotes`), que verifica que `doNotify()` SEPARE error de
transporte, respuesta no interpretable y error de render posterior a una respuesta válida (una respuesta exitosa nunca se convierte en un falso
«conexión interrumpida»), que `resultMessage()` ya no referencie la variable obsoleta `prueba` (sin ReferenceError en resultado parcial), y que el
incierto no se reintente solo. La reconciliación del badge con la auditoría durable (backend/template) se cubre aparte."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "operations_timenotes.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_operations_timenotes_notify_js_invariants():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"operations_timenotes notify JS failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL OPERATIONS TIMENOTES NOTIFY INVARIANTS PASSED" in result.stdout
