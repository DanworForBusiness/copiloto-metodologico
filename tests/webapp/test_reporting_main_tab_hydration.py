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
- El estado de un tab sobrevive visitar otro tab (page-session, sin
  infraestructura de cache nueva).
- NO existe un botón de refresh explícito por tab (retirado, ver sección
  "EXPLICIT REFRESH" abajo) — solo "✕ Limpiar" y el link "Actualizar↗" a
  SharePoint.
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
    # `defaultYearToDateFilters` se RETIRÓ (owner directive — Participación
    # convergió a Año/Estado, MISMO mecanismo que Rentabilidad/HV, ver
    # comercialParticipacionDefaultAnios) — ninguna experiencia usa Desde/
    # Hasta full-history ya. `carteraDefaultFilters` también se RETIRÓ (AÑO/
    # MES MULTI-SELECT corrective) — Año/Mes de Cartera ahora son Sets
    # propios (carteraAnios/carteraMeses) reseteados inline al año/mes
    # calendario actual, MISMO criterio de default sensato que antes.
    body = _function_body("resetFilters")
    assert "state.carteraAnios = new Set([String(new Date().getFullYear())]);" in body
    assert "state.carteraMeses = new Set([String(new Date().getMonth() + 1)]);" in body
    assert "comercialParticipacionDefaultAnios" in body
    assert "defaultYearToDateFilters" not in body
    assert "carteraDefaultFilters" not in body


# ============================================================================
# MAIN-TAB HYDRATION — 1 status compartido por main tab, nunca 1 por
# perspectiva interna (a diferencia de Cartera, donde CADA perspectiva SÍ
# tiene su propio fetch físico — ver test_reporting_cartera.py)
# ============================================================================

def test_rentabilidad_both_perspectives_gate_on_the_same_shared_status():
    # HOT WINDOW / COLD HISTORY corrective (HANDOFF §-17): Rentabilidad se
    # upgradeó al MISMO patrón de dispatcher que Cartera/Horas Vendidas —
    # los sub-renderers solo verifican el status (nunca disparan su propio
    # load independiente). SHARED REPORTING INITIAL-LOAD CONTRACT (HANDOFF
    # §-18): el trigger de hidratación se centralizó en el shell
    # (`TAB_HYDRATION_TRIGGERS.rentabilidad`, ver `render()`) — el
    # dispatcher de experiencia ya NO lo dispara él mismo.
    resumen = _function_body("renderRentabilidadResumen")
    cert = _function_body("renderRentabilidadCertEjecutadas")
    for body in (resumen, cert):
        assert "loadRentabilidadData()" not in body
        assert 'state.rentabilidadDataStatus !== "ready"' in body
    dispatcher = _function_body("renderRentabilidad")
    assert "hydrateRentabilidadTab();" not in dispatcher
    assert "rentabilidad: hydrateRentabilidadTab," in _REPORTING_JS


def test_capacidad_both_perspectives_gate_on_the_same_shared_status():
    # SHARED REPORTING INITIAL-LOAD CONTRACT: el trigger se centralizó en el
    # shell (`TAB_HYDRATION_TRIGGERS.capacidad`, ver `render()`). HOT WINDOW/
    # COLD HISTORY corrective (owner decision 2026-08-18): el bloque ahora
    # ADEMÁS dispara `loadCapacidadAnios` (metadata liviana, en paralelo).
    trigger_block = _REPORTING_JS[_REPORTING_JS.index("capacidad: () => {"):_REPORTING_JS.index("capacidad: () => {") + 250]
    assert 'if (state.batchBDataStatus === "idle") loadBatchBData();' in trigger_block
    productivas = _function_body("renderCapacidadProductivas")
    no_asignadas = _function_body("renderCapacidadNoAsignadas")
    for body in (productivas, no_asignadas):
        assert 'state.batchBDataStatus === "idle"' not in body
        assert "loadBatchBData()" not in body
        assert 'state.batchBDataStatus !== "ready"' in body


def test_horas_vendidas_perspective_switch_is_pure_local_regrouping():
    # Proyecto/Client Partner son DOS agrupaciones del MISMO HORASVENDIDAS_ROWS
    # ya hidratado — nunca un fetch propio por perspectiva. HV tiene UN status
    # POR TAB (`horasVendidasDataStatus`, ciclo de vida scoped propio — salió de
    # batch-a, ver scope decoupling), NUNCA uno por perspectiva.
    # `HORASVENDIDAS_HIERARCHY` decide SOLO las keys del árbol. SHARED
    # REPORTING INITIAL-LOAD CONTRACT: el trigger se centralizó en el shell
    # (`TAB_HYDRATION_TRIGGERS.horasvendidas`, ver `render()`).
    assert "horasvendidas: hydrateHorasVendidasTab," in _REPORTING_JS
    body = _function_body("renderHorasVendidas")
    assert 'state.horasVendidasDataStatus === "idle"' not in body
    assert "hydrateHorasVendidasTab()" not in body
    assert "HORASVENDIDAS_HIERARCHY[persp]" in body
    # Ningún status POR PERSPECTIVA — el único `*Status` del body es el status
    # de tab (horasVendidasDataStatus); no hay horasVendidasProyectoStatus etc.
    assert re.search(r"\bstate\.\w*Status\b", body.replace("state.horasVendidasDataStatus", "")) is None


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
# EXPLICIT REFRESH — RETIRADO (owner feedback: rompía el layout de acciones,
# el "Actualizar↗" a SharePoint ya alcanza). Guard de regresión: no debe
# reintroducirse el botón/mecanismo sin una razón arquitectónica fuerte
# explícita (ver HANDOFF, corrective "TAB-LEVEL HYDRATION + PERSPECTIVE
# STATE" v2).
# ============================================================================

def test_explicit_refresh_button_was_deliberately_removed():
    assert "invalidateExperience" not in _REPORTING_JS
    assert "EXPERIENCE_INVALIDATORS" not in _REPORTING_JS
    assert "idleIfNotLoading" not in _REPORTING_JS
    assert 'onclick="invalidateExperience' not in _REPORTING_JS


def test_filter_bar_actions_keep_only_limpiar_and_the_sharepoint_link():
    body = _function_body("filterBarShell")
    assert "✕ Limpiar" in body
    assert "updateHtml" in body
    assert "refreshHtml" not in body


# ============================================================================
# PAGE-SESSION CACHE — visitar otro main tab nunca resetea el status de uno
# ya hidratado (sin infraestructura de cache nueva: `state` module-level ya
# alcanza)
# ============================================================================

def test_set_experience_only_switches_the_active_tab_never_touches_data_status():
    body = _function_body("setExperience")
    # Cambia el tab activo + emite telemetría de tab_view (beacon liviano, NO es
    # una carga de datos) + render. NUNCA toca un status de datos ni dispara un
    # loader/hidratación desde el cambio de tab (eso lo maneja render()).
    assert "state.experience = id;" in body
    assert 'reportingTelemetry("reporting.tab_view", { report_tab: id });' in body
    assert "render();" in body
    assert "DataStatus" not in body and "load" not in body and "hydrate" not in body and "fetch" not in body


# ============================================================================
# WIDE TABLE LAYOUT — toda `table.dashboard-table` vive dentro de
# `.matrix-scroll` (owner feedback: la tabla Certificadas vs Ejecutadas
# quedaba apretada a la derecha con margen de sobra a la izquierda — le
# faltaba el wrapper `overflow-x:auto` que TODAS las demás tablas anchas de
# Reporting ya usan, `table-layout:fixed` + columnas numéricas de ancho
# fijo fuerza el layout a comprimirse sin él).
# ============================================================================

def test_every_dashboard_table_is_wrapped_in_matrix_scroll():
    import re
    for m in re.finditer(r'<table class="dashboard-table">', _REPORTING_JS):
        preceding = _REPORTING_JS[max(0, m.start() - 80):m.start()]
        assert '<div class="matrix-scroll">' in preceding, (
            f"table.dashboard-table sin wrapper .matrix-scroll cerca de offset {m.start()}"
        )
