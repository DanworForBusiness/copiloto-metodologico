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
    row_fn = _COMMERCIAL_JS.split("function commercialLicensingProfileRowHtml(", 1)[1].split("\n}", 1)[0]
    for field in ("a.name", "a.partner_de_licencia", "a.ae_sap", "a.arquitectura_actual",
                  "a.monto_contrato", "a.vencimiento_contrato", "a.tipo_contrato_sap", "a.segmento_cuenta",
                  "a.seller"):
        assert field in row_fn, field
    # Nunca un campo derivado de OPORTUNIDAD colándose en la fila de cuenta.
    for banned in ("a.opportunity", "a.hours", "a.probability", "a.stage"):
        assert banned not in row_fn, banned


def test_licencias_opportunities_table_uses_the_shared_row_renderer_unmodified_for_detalle():
    # Ingreso esperado/clickable son OPCIONALES (`showRevenue`/`clickable`),
    # default `false` — Detalle sigue llamando `commercialOppDetailTableHtml`
    # SIN pasarlos, cero cambio de comportamiento ahí.
    detalle_call = _COMMERCIAL_JS.split("function commercialDetalleTableHtml(", 1)[1].split("\n}", 1)[0]
    assert "showRevenue" not in detalle_call
    assert "clickable" not in detalle_call
    licencias_call = _COMMERCIAL_JS.split("function commercialLicenciasTableHtml(", 1)[1].split("\n}", 1)[0]
    assert "showRevenue: true" in licencias_call
    assert "clickable: true" in licencias_call


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


def test_licenciamiento_filter_replaces_the_old_hardcoded_predicate():
    # Owner "Explicit Licensing Filter + Profile Scope" §1/§3: el hardcode
    # `o.licencias === "Si"` en `commercialLicenciasAllRows` desapareció —
    # ahora es un filtro EXPLÍCITO ("si"/"no"/"todos"), aplicado por
    # `commercialLicenciasLocallyFilteredRows`, nunca ciego en el universo.
    all_rows_fn = _COMMERCIAL_JS.split("function commercialLicenciasAllRows(", 1)[1].split("\n}", 1)[0]
    assert 'licencias === "Si"' not in all_rows_fn
    filtered_fn = _COMMERCIAL_JS.split("function commercialLicenciasLocallyFilteredRows(", 1)[1].split("\n}", 1)[0]
    assert 'fLic === "si" && o.licencias !== "Si"' in filtered_fn
    # "no" es el COMPLEMENTO de "Si" (corregido tras validación en vivo —
    # ver HANDOFF): la property "Licencias" en Odoo casi nunca se completa
    # explícitamente en "No", la mayoría queda "Sin definir" (normalizado,
    # nunca null técnico) — un match EXACTO contra el string "No" dejaba el
    # filtro prácticamente vacío siempre. NUNCA volver a `!== "No"`.
    assert 'fLic === "no" && o.licencias === "Si"' in filtered_fn
    assert 'o.licencias !== "No"' not in filtered_fn
    assert "licLicenciamientoFilter: \"si\"" in _COMMERCIAL_JS, "default state es \"si\" (reproduce el viejo hardcode)"


def test_profile_scope_derives_accounts_by_id_never_by_name():
    # Owner §8: "Do NOT compare/join by display name."
    fn = _COMMERCIAL_JS.split("function commercialLicenciasVisibleAccountIds(", 1)[1].split("\n}", 1)[0]
    assert "o.account_id" in fn
    assert ".name" not in fn


def test_licensing_profile_route_is_registered_read_only():
    router_src = (_ROOT / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
    assert router_src.count('@router.get("/comercial/data/licensing-profile"') == 1
    surface = (_ROOT / "tests" / "route_surface.json").read_text(encoding="utf-8")
    assert "GET /comercial/data/licensing-profile" in surface
