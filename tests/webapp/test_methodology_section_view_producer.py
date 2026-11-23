"""Productor de `section_view` metodológico (scroll-spy de app.js): corre el harness node que ejerce el
`emitSectionView` REAL extraído de webapp/static/app.js (slug con guiones, rechazo de texto arbitrario, se omite
el hero, deduplicación por visita+sección, scroll-gate + debounce)."""
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("methodology_section_view_producer.node.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está disponible")
def test_methodology_section_view_producer_harness():
    r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
