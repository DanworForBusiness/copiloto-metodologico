"""test_app_table_ergonomics.py — contrato del primitivo COMPARTIDO de
ergonomía de tablas (Director §18-27/§36, ver HANDOFF
commercial_licensing_20260925): reorder (`initDraggableColumns`, ya existía) +
resize (`initResizableColumns`, nuevo) viven en app.js, son opt-in por
atributo (`data-draggable-columns-key`/`data-resizable-columns-key`) y no
saben nada de negocio — se prueban acá contra una tabla SINTÉTICA sin relación
con Comercial, precisamente para demostrar que son genuinamente
table-agnostic, no una segunda copia de la lógica del Perfil de licenciamiento
disfrazada de "primitivo compartido".

Los contratos ESPECÍFICOS de Comercial (Perfil + Oportunidades, sus columnas
de negocio, la fila de Total order-aware) están en
test_commercial_licensing_table_ergonomics.py.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "app_table_ergonomics.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_app_table_ergonomics_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"App table ergonomics failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL APP TABLE ERGONOMICS INVARIANTS PASSED" in result.stdout
