"""
test_commercial_licensing_profile_frontend.py — Account Licensing Profile
(ver HANDOFF — Commercial Licensing), superficie FRONTEND.

Las invariantes de comportamiento (contenedor de dos secciones, Ingreso
esperado exclusivo de Licencias, cross-filter, reactividad de las DOS tablas
al cambio de Tipo, ausencia de stale rows) viven en
commercial_licensing_profile.node.js, que ejecuta el motor REAL. Acá quedan
las de ARCHIVO/regresión que no requieren el harness completo — ver también
test_commercial_licensing_profile.py (backend/servicio).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "commercial_licensing_profile.node.js"
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_licensing_profile_frontend_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Licensing profile frontend invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL LICENSING PROFILE FRONTEND INVARIANTS PASSED" in result.stdout


def test_licensing_profile_has_its_own_independent_loader():
    assert _COMMERCIAL_JS.count("async function commercialLoadLicensingProfile(") == 1
    body = _COMMERCIAL_JS.split("async function commercialLoadLicensingProfile(", 1)[1].split("\n}", 1)[0]
    assert "/comercial/data/licensing-profile" in body
    assert "commercialLoadOpportunitiesDetail" not in body
    assert "opportunitiesDetail" not in body, "el perfil nunca lee/escribe el estado de Detalle/Licencias"


def test_opening_licencias_triggers_both_independent_loads():
    body = _COMMERCIAL_JS.split("function commercialSetCapability(", 1)[1].split("\n}", 1)[0]
    assert 'capability === "licencias" || capability === "detalle"' in body
    assert "commercialLoadLicensingProfile();" in body
    # Detalle NUNCA dispara el perfil — el guard exige `capability === "licencias"`.
    detalle_gate = body.split("commercialLoadLicensingProfile();", 1)[0].splitlines()[-3:]
    assert any('capability === "licencias"' in line for line in detalle_gate)


def test_tipo_change_reloads_licencias_tables_when_active_but_leaves_other_tabs_as_before():
    # Owner §2 (ver HANDOFF): con Licencias activa, cambiar Tipo recarga LAS
    # DOS de inmediato — fix ACOTADO a esta capability, deliberadamente sin
    # tocar el mismo gap pre-existente en Detalle/Clientes/etc. (fuera de
    # alcance de este ticket, ver audit previo).
    fn = _COMMERCIAL_JS.split("function commercialSetTipo(", 1)[1].split("\ndocument.addEventListener", 1)[0]
    assert 'capability === "licencias"' in fn
    assert "commercialLoadOpportunitiesDetail();" in fn
    assert "commercialLoadLicensingProfile();" in fn


def test_es_cliente_is_never_a_visible_filter_or_column():
    # Owner §4: internal-only para v1 — nunca en el markup del Perfil.
    profile_block = _js_code(_COMMERCIAL_JS).split(
        "function commercialLicensingProfileRowHtml(", 1)[1].split(
        "function commercialLicensingProfileHtml(", 1)[0]
    assert "Es Cliente" not in profile_block
    assert "esCliente" not in profile_block.replace(" ", "")


def test_licensing_profile_columns_are_account_grain_only():
    # Director §9/§10 (ver HANDOFF): las celdas ahora son data-driven —
    # cada campo vive en su propio `cell(a)` DENTRO de
    # `COMMERCIAL_LICENSING_PROFILE_COLUMNS` (para poder reordenarse),
    # nunca hardcodeado inline en `commercialLicensingProfileRowHtml` como
    # antes de esta fase. La aserción se mueve a esa definición.
    columns_block = _COMMERCIAL_JS.split(
        "const COMMERCIAL_LICENSING_PROFILE_COLUMNS = [", 1)[1].split("\n];", 1)[0]
    for field in ("a.name", "a.partner_de_licencia", "a.ae_sap", "a.arquitectura_actual",
                  "a.monto_contrato", "a.vencimiento_contrato", "a.tipo_contrato_sap", "a.segmento_cuenta",
                  "a.seller"):
        assert field in columns_block, field
    # Nunca un campo derivado de OPORTUNIDAD colándose en la fila de cuenta
    # — ni en la definición de columnas ni en el renderer de fila en sí.
    row_fn = _COMMERCIAL_JS.split("function commercialLicensingProfileRowHtml(", 1)[1].split("\n}", 1)[0]
    for banned in ("a.opportunity", "a.hours", "a.probability", "a.stage"):
        assert banned not in columns_block, banned
        assert banned not in row_fn, banned


def test_licencias_opportunities_table_never_calls_the_shared_opp_detail_renderer():
    # Director §1 (ver HANDOFF): Table A pasa a ser una tabla COMPACTA
    # PROPIA (`commercialLicenciasCompactTableHtml`) — ya NO llama a
    # `commercialOppDetailTableHtml` en absoluto (antes lo hacía con
    # `showRevenue`/`clickable` en `true`). Garantía MÁS fuerte que antes:
    # Licencias no puede volver a afectar accidentalmente a Detalle ni
    # siquiera a través de esos flags compartidos, porque ya no comparten
    # función. Detalle sigue exactamente igual (13 columnas, sin cambios).
    detalle_call = _COMMERCIAL_JS.split("function commercialDetalleTableHtml(", 1)[1].split("\n}", 1)[0]
    assert "commercialOppDetailTableHtml" in detalle_call
    assert "showRevenue" not in detalle_call
    assert "clickable" not in detalle_call
    licencias_call = _COMMERCIAL_JS.split("function commercialLicenciasTableHtml(", 1)[1].split("\n}", 1)[0]
    assert "commercialOppDetailTableHtml" not in licencias_call
    assert "commercialLicenciasCompactTableHtml" in licencias_call


def test_bidirectional_drilldown_state_is_one_shape_never_persisted():
    # Owner "Bidirectional Account Drilldown" §8: UN solo estado sirve las
    # DOS direcciones — nunca `opportunitySelectedClient`+`profileSelectedClient`
    # paralelos. Y nunca viaja en ningún body de fetch/POST (es foco
    # client-side puro, nunca estado del servidor).
    assert _COMMERCIAL_JS.count("licensingDrilldownAccountId") >= 3
    assert _COMMERCIAL_JS.count("licensingDrilldownOrigin") >= 3
    assert "licensingSelectedAccountId" not in _COMMERCIAL_JS, "el estado viejo (presentación-only) fue reemplazado"
    fetch_lines = [l for l in _COMMERCIAL_JS.splitlines() if "fetch(" in l and "licensing" in l.lower()]
    for line in fetch_lines:
        assert "licensingDrilldownAccountId" not in line
        assert "licensingDrilldownOrigin" not in line


def test_licensing_close_drilldown_is_a_single_function_for_both_directions():
    # Owner "Bidirectional Account Drilldown" §8/§O: un solo primitivo de
    # cierre, nunca dos `close*CrossFilter` paralelas.
    assert _COMMERCIAL_JS.count("function commercialLicensingCloseDrilldown(") == 1
    assert "commercialLicensingProfileCloseCrossFilter" not in _COMMERCIAL_JS
    body = _COMMERCIAL_JS.split("function commercialLicensingCloseDrilldown(", 1)[1].split("\n}", 1)[0]
    assert 'origin === "opportunities"' in body
    assert "data-commercial-licensing-account-row" in body
    assert "data-commercial-licensing-profile-account-row" in body


def test_table_a_uses_a_fixed_licenciamiento_si_predicate_no_selector():
    # Owner "Final Business Semantics Realignment" §2: el selector
    # "Licenciamiento" se retiró — restaura el invariante FIJO original.
    all_rows_fn = _COMMERCIAL_JS.split("function commercialLicenciasAllRows(", 1)[1].split("\n}", 1)[0]
    assert 'o.licencias === "Si"' in all_rows_fn
    assert "licLicenciamientoFilter" not in _COMMERCIAL_JS
    assert "data-commercial-licencias-licenciamiento-filter" not in _COMMERCIAL_JS
    assert "commercialLicenciasVisibleAccountIds" not in _COMMERCIAL_JS
    assert "commercialLicensingProfileRefresh" not in _COMMERCIAL_JS
    assert "licProfileScope" not in _COMMERCIAL_JS
    assert "data-commercial-licprofile-scope-filter" not in _COMMERCIAL_JS


def test_table_b_filtered_rows_never_reference_table_a_account_ids():
    # Owner §3/§5: el Perfil ya NO deriva su universo de las oportunidades
    # visibles — `commercialLicensingProfileFilteredRows` no debe leer
    # ningún estado de Oportunidades salvo el drilldown contextual explícito
    # (`licensingDrilldownAccountId`/`Origin`, que SÍ es una excepción
    # legítima y documentada).
    fn = _COMMERCIAL_JS.split("function commercialLicensingProfileFilteredRows(", 1)[1].split("\n}", 1)[0]
    assert "commercialLicenciasAllRows" not in fn
    assert "commercialLicenciasFilteredRows" not in fn
    assert "licStageFilter" not in fn
    assert "licSellerFilter" not in fn
    assert "licSearchText" not in fn


def test_global_tipo_change_does_not_alter_profile_local_filter_semantics():
    # Owner §6: "Table B must NOT change its base universe when the user
    # switches tipo" — el backend ya devuelve el MISMO resultado sin
    # importar tipo (ver test_commercial_licensing_profile.py); acá se fija
    # que el frontend no reintroduzca un mapeo tipo->Es Cliente?.
    assert "Es Cliente? No" not in _COMMERCIAL_JS
    assert "Es Cliente? Sí" not in _COMMERCIAL_JS


def test_profile_empty_state_no_longer_means_not_profile_eligible():
    # CONTRATO NUEVO (Director, "Ver detalle de licencias"): "no es elegible
    # para el Perfil" dejó de ser un resultado terminal — esa cuenta se
    # inspecciona igual. El vacío queda reservado para lo que de verdad es un
    # vacío: no poder resolver la cuenta.
    #
    # Lo cubre end-to-end test_commercial_licensing_contextual_detail.py; acá
    # sólo se fija que el mensaje viejo no vuelva.
    fn = _COMMERCIAL_JS.split("function commercialLicensingProfileTableHtml(", 1)[1].split("\n}", 1)[0]
    assert "no está marcada como cliente" not in fn
    assert 'mode === "detail"' in fn
    assert "No pudimos resolver la cuenta" in fn


def test_licensing_profile_route_is_registered_read_only():
    router_src = (_ROOT / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
    assert router_src.count('@router.get("/comercial/data/licensing-profile"') == 1
    surface = (_ROOT / "tests" / "route_surface.json").read_text(encoding="utf-8")
    assert "GET /comercial/data/licensing-profile" in surface
