"""
test_commercial_bucket1_tabs.py — Owner ticket "COMMERCIAL TABS — BUCKET 1:
LICENCIAS + HISTORIAL PERDIDAS + DETALLE OPORTUNIDADES" (ver HANDOFF). Ver
commercial_bucket1_tabs.node.js para las invariantes del motor real: el
filtro CLIENT-SIDE de Licencias (universo no degenerado, atravesando
prospecto/activa/suspendida/ganada — nunca reducido a "sólo activas"),
Detalle Oportunidades sin ese filtro, la carga LAZY COMPARTIDA entre
Licencias y Detalle (una sola request para las dos tabs), Historial
Perdidas con endpoint/estado totalmente separados, zero-refetch en
filtros/orden/búsqueda locales, y el reintento escopeado single-flight.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_bucket1_tabs.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_bucket1_tabs_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Commercial Bucket 1 tabs invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL BUCKET 1 TABS INVARIANTS PASSED" in result.stdout
