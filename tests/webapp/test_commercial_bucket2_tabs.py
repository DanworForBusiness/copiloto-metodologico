"""
test_commercial_bucket2_tabs.py — Owner ticket "COMMERCIAL TABS — BUCKET 2:
CLIENTES + LEADS" (ver HANDOFF). Ver commercial_bucket2_tabs.node.js para las
invariantes del motor real: carga LAZY de Leads/Clientes al abrir su
capability (una sola request), Ex Clientes LAZY DENTRO del sub-tab de
Clientes (recién al abrirlo, nunca junto con Clientes), zero-refetch en
expandir/ordenar/filtrar/buscar locales de las tres vistas, el reintento
escopeado single-flight por dataset (leads/clients/ex-clients, cada uno su
propio endpoint), y tipo-safety (descarte silencioso de una respuesta
obsoleta si Todos/NN/BI cambió mientras la request viajaba).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_bucket2_tabs.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_bucket2_tabs_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Commercial Bucket 2 tabs invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL BUCKET 2 TABS INVARIANTS PASSED" in result.stdout
