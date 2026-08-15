"""
test_reporting_main_tab_hydration.py — Contrato de hidratación MAIN TAB
(Reporting Integral, owner corrective "TAB-LEVEL HYDRATION + PERSPECTIVE
STATE"). Source-seam, sin runtime JS (mismo criterio que
test_reporting_cartera.py/test_reporting_batch_a.py): cruza las 6
experiencias en vez de profundizar en una — el detalle de Cartera (Año/Mes,
ventana caliente) vive en test_reporting_cartera.py, el de Batch A
(Horas Vendidas/Participación/Calendario/Salud) en
test_reporting_batch_a.py. Acá se prueba el CONTRATO TRANSVERSAL:

- setPerspective NUNCA resetea el filtro de ninguna experiencia (perspective-
  local filter state).
- Las 5 experiencias no-Cartera gatean CADA perspectiva interna contra un
  ÚNICO status compartido por main tab (nunca uno por perspectiva) — un
  cambio de perspectiva interna nunca dispara un fetch nuevo.
- El refresh explícito invalida SOLO su propio main tab/source-family,
  nunca global, y nunca pisa un fetch ya en vuelo.
- El estado de un tab sobrevive visitar otro tab (page-session, sin
  infraestructura de cache nueva).
"""
from __future__ import annotations

import re
from pathlib import Path

_REPORTING_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "reporting.js").read_text(encoding="utf-8")


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


# ============================================================================
# PERSPECTIVE-LOCAL FILTER STATE — setPerspective nunca resetea filtros
# ============================================================================

def test_set_perspective_never_touches_state_filters():
    body = _function_body("setPerspective")
    # Ninguna ASIGNACIÓN a state.filters (el comentario explicando esto SÍ
    # menciona la string "state.filters" en prosa — se busca la sintaxis de
    # asignación real, `state.filters... = ...`, no un `==`/`!=`/backtick).
    assert re.search(r"state\.filters[\w.\[\]]*\s*=(?!=)", body) is None
    assert "state.perspective[expId] = p;" in body


def test_reset_filters_still_applies_defaults_explicitly_on_limpiar():
    # "Limpiar" SIGUE siendo el único lugar donde se reaplica un default —
    # acción explícita del usuario, nunca implícita en un cambio de vista.
    body = _function_body("resetFilters")
    assert "carteraDefaultFilters" in body
    assert "defaultYearToDateFilters" in body


# ============================================================================
# MAIN-TAB HYDRATION — 1 status compartido por main tab, nunca 1 por
# perspectiva interna (a diferencia de Cartera, donde CADA perspectiva SÍ
# tiene su propio fetch físico — ver test_reporting_cartera.py)
# ============================================================================

def test_rentabilidad_both_perspectives_gate_on_the_same_shared_status():
    resumen = _function_body("renderRentabilidadResumen")
    cert = _function_body("renderRentabilidadCertEjecutadas")
    for body in (resumen, cert):
        assert 'state.rentabilidadDataStatus === "idle"' in body
        assert "loadRentabilidadData()" in body
        assert 'state.rentabilidadDataStatus !== "ready"' in body


def test_capacidad_both_perspectives_gate_on_the_same_shared_status():
    productivas = _function_body("renderCapacidadProductivas")
    no_asignadas = _function_body("renderCapacidadNoAsignadas")
    for body in (productivas, no_asignadas):
        assert 'state.batchBDataStatus === "idle"' in body
        assert "loadBatchBData()" in body
        assert 'state.batchBDataStatus !== "ready"' in body


def test_horas_vendidas_perspective_switch_is_pure_local_regrouping():
    # Proyecto/Client Partner son DOS agrupaciones del MISMO
    # HORASVENDIDAS_ROWS ya hidratado por Batch A — nunca un fetch propio
    # por perspectiva (a diferencia de Cartera). `HORASVENDIDAS_HIERARCHY`
    # decide SOLO las keys del árbol, nunca un status.
    body = _function_body("renderHorasVendidas")
    assert 'state.batchADataStatus === "idle"' in body
    assert "loadBatchAData()" in body
    assert "HORASVENDIDAS_HIERARCHY[persp]" in body
    # Ningún OTRO status por perspectiva (ej. "horasVendidasProyectoStatus")
    # — solo el único status compartido de Batch A.
    assert re.search(r"\bstate\.\w*Status\b", body.replace("state.batchADataStatus", "")) is None


def test_no_perspective_has_its_own_data_status_outside_cartera():
    # Barrido transversal: fuera de `carteraMantenimientoStatus`/
    # `carteraProyectoStatus` (Cartera, donde SÍ es correcto — 2 fetches
    # físicos distintos), ninguna otra experiencia debe introducir un
    # status por perspectiva interna — el contrato es 1 status POR MAIN
    # TAB para las 5 restantes.
    forbidden = [
        "rentabilidadResumenStatus", "rentabilidadCertStatus",
        "horasVendidasProyectoStatus", "horasVendidasClientPartnerStatus",
        "comercialParticipacionStatus", "comercialSaludStatus",
        "calendarioFrecuenciaStatus", "calendarioClientPartnerStatus",
        "capacidadProductivasStatus", "capacidadNoAsignadasStatus",
    ]
    for name in forbidden:
        assert name not in _REPORTING_JS


# ============================================================================
# EXPLICIT REFRESH — invalidación por main tab/source-family, nunca global,
# nunca pisa un fetch en vuelo
# ============================================================================

def test_invalidate_experience_dispatch_table_covers_all_six_tabs():
    body = _function_body("invalidateExperience")
    assert "EXPERIENCE_INVALIDATORS[expId]" in body
    table = _REPORTING_JS[_REPORTING_JS.index("const EXPERIENCE_INVALIDATORS"):_REPORTING_JS.index("function invalidateExperience")]
    for exp_id in ("cartera:", "rentabilidad:", "horasvendidas:", "comercial:", "calendario:", "capacidad:"):
        assert exp_id in table


def test_invalidate_cartera_never_touches_other_tabs_status():
    table = _REPORTING_JS[_REPORTING_JS.index("const EXPERIENCE_INVALIDATORS"):_REPORTING_JS.index("function invalidateExperience")]
    cartera_entry = table[table.index("cartera:"):table.index("rentabilidad:")]
    for other in ("rentabilidadDataStatus", "batchADataStatus", "batchBDataStatus"):
        assert other not in cartera_entry


def test_invalidate_horasvendidas_and_siblings_share_batch_a_by_design_not_by_accident():
    # horasvendidas/comercial/calendario invalidan el MISMO
    # `batchADataStatus` — correcto (comparten un único fetch físico real,
    # ver HANDOFF/real-data-lineage.md), nunca invalidan rentabilidad/
    # capacidad/cartera (source-families genuinamente distintas).
    table = _REPORTING_JS[_REPORTING_JS.index("const EXPERIENCE_INVALIDATORS"):_REPORTING_JS.index("function invalidateExperience")]
    for exp_id in ("horasvendidas:", "comercial:", "calendario:"):
        entry_start = table.index(exp_id)
        entry = table[entry_start:entry_start + 120]
        assert "batchADataStatus" in entry
        assert "rentabilidadDataStatus" not in entry
        assert "batchBDataStatus" not in entry


def test_invalidate_never_overwrites_an_in_flight_fetch():
    assert "function idleIfNotLoading(current)" in _REPORTING_JS
    guard = _function_body("idleIfNotLoading")
    assert '"loading"' in guard
    table = _REPORTING_JS[_REPORTING_JS.index("const EXPERIENCE_INVALIDATORS"):_REPORTING_JS.index("function invalidateExperience")]
    # 8 llamados: Cartera invalida 3 status propios (Mantenimiento/Proyecto/
    # metadata de años), las otras 5 experiencias 1 cada una — TODOS pasan
    # por el guard, ninguno pisa "loading" con "idle" a mano.
    assert table.count("idleIfNotLoading(") == 8
    assert "= \"idle\";" not in table  # ninguna asignación directa sin el guard


# ============================================================================
# PAGE-SESSION CACHE — visitar otro main tab nunca resetea el status de uno
# ya hidratado (sin infraestructura de cache nueva: `state` module-level ya
# alcanza)
# ============================================================================

def test_set_experience_only_switches_the_active_tab_never_touches_data_status():
    body = _function_body("setExperience")
    assert body.strip() == "{\n  state.experience = id;\n  render();\n}"
