"""
test_reporting_initial_load_contract.py — SHARED REPORTING INITIAL-LOAD
CONTRACT (owner: "OWNER LIVE UX CORRECTIVE — APPLY TO ALL REPORTING MAIN
TABS"). Durante la PRIMERA hidratación de un tab principal se muestra SOLO
navegación de tabs + loader inicial compartido — filtros/KPIs/secciones
entran TODOS JUNTOS recién cuando hay contenido usable. Una carga
incremental posterior (año frío, error parcial) NUNCA revierte una
experiencia ya hidratada a la pantalla de loader inicial. Renderiza la
`render()` REAL contra estado sintético — same pattern as
reporting_capacidad_composition.node.js / reporting_participacion_numerator.
node.js.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "reporting_initial_load_contract.node.js"
_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_initial_load_contract_invariants_hold():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"Initial-load contract invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL REPORTING INITIAL-LOAD CONTRACT INVARIANTS PASSED" in result.stdout


# --- Source-seam: implementación COMPARTIDA, nunca 6 variantes locales -----

def _function_body(name: str) -> str:
    start = _REPORTING_JS.index(f"function {name}(")
    brace = _REPORTING_JS.index("{", start)
    depth = 0
    for i in range(brace, len(_REPORTING_JS)):
        if _REPORTING_JS[i] == "{":
            depth += 1
        elif _REPORTING_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return _REPORTING_JS[brace:i + 1]
    raise AssertionError(f"no se encontró el cierre de function {name}(")


def test_render_has_no_per_tab_presentation_conditionals():
    # SHARED IMPLEMENTATION requirement (owner: "Do NOT implement six local
    # variants... prefer one shared predicate/helper") — `render()` decide
    # SOLO en base al flag compartido `state.reportingTabHydrated[expId]` +
    # `reportingTabRawStatus(expId)`, nunca con un `if (tab === "cartera")`/
    # `if (expId === "rentabilidad")` estilo por-experiencia para elegir
    # entre loader/filtros/contenido.
    body = _function_body("render")
    assert not re.search(r'(tab|expId)\s*===\s*"(cartera|rentabilidad|horasvendidas|comercial|calendario|capacidad)"', body)
    assert "state.reportingTabHydrated[expId]" in body


def test_reporting_tab_raw_status_is_a_single_shared_accessor():
    # `reportingTabRawStatus` es el ÚNICO lugar que sabe qué campo(s) de
    # status crudo le corresponden a cada tab (combinando fuentes cuando
    # aplica, ver Cartera/Comercial) — el resto del shell (`render()`) lo
    # trata como una caja negra, nunca reimplementa el mapeo.
    assert "function reportingTabRawStatus(expId)" in _REPORTING_JS
    body = _function_body("reportingTabRawStatus")
    assert "switch (expId)" in body


def test_hydration_triggers_are_centralized_in_one_map():
    # `TAB_HYDRATION_TRIGGERS` es el ÚNICO punto que dispara la hidratación
    # de cada tab — `render()` la invoca incondicionalmente ANTES de decidir
    # qué mostrar (si no, un tab nunca visitado nunca saldría de "idle").
    assert "const TAB_HYDRATION_TRIGGERS = {" in _REPORTING_JS
    for exp_id in ("cartera", "rentabilidad", "horasvendidas", "comercial", "calendario", "capacidad"):
        assert f"{exp_id}:" in _REPORTING_JS[_REPORTING_JS.index("const TAB_HYDRATION_TRIGGERS = {"):_REPORTING_JS.index("function reportingTabRawStatus")]
    render_body = _function_body("render")
    assert "if (TAB_HYDRATION_TRIGGERS[expId]) TAB_HYDRATION_TRIGGERS[expId]();" in render_body


def test_reporting_tab_hydrated_is_a_one_way_flag_never_reset():
    # STATE MODEL requirement: NUNCA se apaga (ni por perspectiva individual
    # todavía no lista, ni por un error incremental posterior) — se busca
    # cualquier asignación `state.reportingTabHydrated[...] = false` en TODO
    # el archivo (debería no existir ninguna, solo el literal inicial en
    # `false` y la asignación a `true` dentro de `render()`).
    assignments_to_false = re.findall(r"state\.reportingTabHydrated\[[^\]]+\]\s*=\s*false", _REPORTING_JS)
    assert assignments_to_false == []
    render_body = _function_body("render")
    assert "state.reportingTabHydrated[expId] = true;" in render_body


def test_no_dispatcher_still_self_triggers_hydration():
    # Barrido transversal (task #143): ningún dispatcher de experiencia
    # dispara su propia hidratación como primera línea — el ÚNICO lugar que
    # llama `hydrateXTab()`/`loadBatchXData()` de forma incondicional o
    # gateada por "idle" es `TAB_HYDRATION_TRIGGERS` (dentro de su propio
    # bloque, delimitado abajo por `function reportingTabRawStatus`).
    triggers_block_start = _REPORTING_JS.index("const TAB_HYDRATION_TRIGGERS = {")
    triggers_block_end = _REPORTING_JS.index("function reportingTabRawStatus")
    rest_of_file = _REPORTING_JS[:triggers_block_start] + _REPORTING_JS[triggers_block_end:]
    for fn in (
        "hydrateCarteraTab", "hydrateRentabilidadTab", "hydrateHorasVendidasTab",
        "hydrateComercialParticipacionTab",
    ):
        # Ninguna otra función del archivo (fuera de su propia definición y
        # del bloque TAB_HYDRATION_TRIGGERS) invoca estos triggers.
        own_def_start = rest_of_file.index(f"function {fn}(")
        own_def_end = rest_of_file.index("\n}\n", own_def_start) + 3
        outside_own_def = rest_of_file[:own_def_start] + rest_of_file[own_def_end:]
        assert f"{fn}();" not in outside_own_def, f"{fn}() todavía se dispara fuera de TAB_HYDRATION_TRIGGERS"
    for pattern in ('state.batchADataStatus === "idle") loadBatchAData()', 'state.batchBDataStatus === "idle") loadBatchBData()'):
        occurrences = rest_of_file.count(pattern)
        assert occurrences == 0, f"'{pattern}' todavía aparece fuera de TAB_HYDRATION_TRIGGERS ({occurrences}x)"
