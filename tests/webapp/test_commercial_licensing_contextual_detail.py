"""COMMERCIAL LICENSING — inspección contextual de UNA cuenta.

"Ver detalle de licencias" terminaba en `0 cuentas` + "no está marcada como
cliente" para cualquier cuenta fuera del universo gobernado del Perfil
(`Es Cliente? = Sí AND Argentina`).

El punto de rechazo era el BACKEND: `_build_licensing_profile` parte de
`_partner_ids_by_cliente_value(...)`, así que una cuenta inelegible nunca entra
al payload; el filtro por id del frontend se quedaba sin filas.

SON DOS CONCEPTOS DISTINTOS. El Perfil es una COLECCIÓN gobernada y su
guardrail no se toca. "Ver detalle de licencias" es una CONSULTA puntual sobre
la cuenta de una oportunidad: pertenecer a la colección no puede ser requisito
para poder mirarla.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.webapp._commercial_fake_odoo import FakeOdooClient
from webapp.services import commercial_service as svc_module
from webapp.services.commercial_service import CommercialService

_ROOT = Path(__file__).resolve().parents[2]
_JS = (_ROOT / "webapp" / "static" / "commercial.js").read_text(encoding="utf-8")


def _sin_comentarios(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


# ── Backend: la consulta puntual resuelve cuentas inelegibles ─────────────

_STAGES = [{"id": 1, "name": "1 - Inicial", "is_won": False}]


def _cuenta(pid, nombre, *, pais=None, cliente=None, commercial=None):
    """Una `res.partner` con properties de licenciamiento."""
    props = {"4495d8705b9b212c": 12345}          # Monto Contrato (ver _LICENSING_PROPERTIES)
    row = {"id": pid, "name": nombre, "active": True,
           "properties": props, "user_id": [7, "Ana"], "currency_id": [1, "USD"]}
    if pais is not None:
        row["country_id"] = pais
    if commercial is not None:
        row["commercial_partner_id"] = commercial
    if cliente is not None:
        row["es_cliente"] = cliente
    return row


def _servicio(cuentas):
    """El fake sirve `res.partner` desde `partners=`, no desde `leads=` (que
    es `crm.lead`). La inspección de licenciamiento lee cuentas, no
    oportunidades."""
    svc_module.clear_cache()
    cliente = FakeOdooClient(stages=_STAGES, tags=[], leads=[], partners=cuentas)
    return CommercialService(cliente), cliente


@pytest.mark.parametrize("descripcion,pais", [
    ("sin país", None),
    ("país no Argentina", [99, "Brasil"]),
])
def test_una_cuenta_fuera_del_universo_se_puede_inspectar(descripcion, pais):
    """B/C/D: `Es Cliente?` en blanco, "No"/"Ex", o no-Argentina — todas
    inspeccionables. Antes, cualquiera de estas terminaba en el empty state."""
    servicio, _ = _servicio([_cuenta(500, "Cuenta Ajena", pais=pais)])
    fila = servicio.get_licensing_account_detail(500)
    assert fila is not None, f"la cuenta con {descripcion} tiene que ser inspeccionable"
    assert fila["id"] == 500
    assert fila["name"] == "Cuenta Ajena"
    svc_module.clear_cache()


def test_j_reusa_el_mismo_adaptador_de_properties_del_perfil():
    """El detalle NO puede tener su propio modelo de licenciamiento: sale del
    MISMO `_licensing_account_property_row` que arma las filas del Perfil."""
    cuerpo = (_ROOT / "webapp" / "services" / "commercial_service.py").read_text(encoding="utf-8")
    inicio = cuerpo.index("def get_licensing_account_detail(")
    fin = cuerpo.index("def get_licensing_profile(", inicio)
    metodo = cuerpo[inicio:fin]
    assert "_licensing_account_property_row(" in metodo
    # Y no reimplementa la lectura de properties.
    assert "_LICENSING_PROPERTIES" not in metodo
    assert "_selection_value" not in metodo and "_monetary_value" not in metodo


def test_i_resuelve_la_identidad_canonica_de_la_cuenta():
    """Si llega el id de un CONTACTO, se inspecciona su cuenta comercial —
    mismo criterio de identidad que usa el Perfil."""
    servicio, cliente = _servicio([
        # El fake guarda `commercial_partner_id` como id ESCALAR y arma el par
        # [id, nombre] al proyectar — igual que Odoo. Pasarle el par ya armado
        # lo anidaría.
        _cuenta(600, "Contacto Juan", commercial=601),
        _cuenta(601, "Empresa Madre"),
    ])
    fila = servicio.get_licensing_account_detail(600)
    assert fila["id"] == 601, "tiene que resolver a la cuenta canónica, no al contacto"
    assert fila["name"] == "Empresa Madre"
    svc_module.clear_cache()


def test_no_hay_n_mas_1_en_la_consulta_puntual():
    """Una consulta en el caso común; una segunda SÓLO si hubo que resolver un
    contacto a su empresa. Nunca una por campo ni por property."""
    servicio, cliente = _servicio([_cuenta(700, "Cuenta Directa")])
    servicio.get_licensing_account_detail(700)
    lecturas = [m for (m, _d, _f) in cliente.calls if m == "res.partner"]
    assert len(lecturas) == 1, f"se esperaba UNA lectura de res.partner, hubo {len(lecturas)}"
    svc_module.clear_cache()


def test_cuenta_inexistente_devuelve_none():
    """El único vacío legítimo: la cuenta no se puede resolver. Distinto de
    "no es elegible para el Perfil"."""
    servicio, _ = _servicio([_cuenta(800, "Existe")])
    assert servicio.get_licensing_account_detail(999999) is None
    svc_module.clear_cache()


# ── El universo gobernado no se ensancha ──────────────────────────────────

def test_e_la_consulta_puntual_no_toca_la_coleccion_gobernada():
    """PRUEBA DEL GUARDRAIL: `_build_licensing_profile` sigue partiendo de
    `Es Cliente? = Sí` y filtrando por Argentina. El detalle contextual vive en
    otro método y no lo roza."""
    cuerpo = (_ROOT / "webapp" / "services" / "commercial_service.py").read_text(encoding="utf-8")
    inicio = cuerpo.index("def _build_licensing_profile(")
    fin = cuerpo.index("def _argentina_country_id(", inicio)
    universo = cuerpo[inicio:fin]
    assert "_partner_ids_by_cliente_value(self._CLIENTE_SI_LABELS)" in universo
    assert "_argentina_country_id()" in universo
    assert "licensing_account_detail" not in universo, (
        "la colección no puede consultar el camino de inspección puntual"
    )


def test_e_el_frontend_guarda_la_cuenta_consultada_aparte():
    """No entra a `licensingProfile.accounts`: vive en `licensingDetailAccount`
    y sólo se muestra mientras la inspección está activa."""
    codigo = _sin_comentarios(_JS)
    assert "licensingDetailAccount" in codigo
    assert "licensingProfile.accounts.push" not in codigo
    assert "licensingProfile.accounts.concat" not in codigo


def test_f_cerrar_restaura_el_universo_gobernado():
    codigo = _sin_comentarios(_JS)
    inicio = codigo.index("function commercialLicensingCloseDrilldown(")
    cuerpo = codigo[inicio:inicio + 700]
    assert "licensingDetailAccount = null" in cuerpo
    assert "licensingDrilldownAccountId = null" in cuerpo
    assert "licensingDrilldownOrigin = null" in cuerpo


def test_g_la_cuenta_inspeccionada_no_expone_accion_de_prioridad():
    """La prioridad clasifica el universo gobernado. Una cuenta ajena no puede
    entrar a ese flujo por la puerta de atrás."""
    codigo = _sin_comentarios(_JS)
    inicio = codigo.index("const actionCell = mode ===")
    cuerpo = codigo[inicio:inicio + 600]
    assert 'mode === "detail"' in cuerpo
    detalle = cuerpo[:cuerpo.index('mode === "secondary"')]
    assert "licprofile-defer" not in detalle and "licprofile-restore" not in detalle, (
        "el modo detalle no puede ofrecer mover/restaurar"
    )


def test_h_la_tabla_secundaria_no_se_ve_afectada():
    codigo = _sin_comentarios(_JS)
    assert "const secondaryRows = contextualOnly ? [] :" in codigo, (
        "con inspección contextual activa, la secundaria no deriva de esa fila"
    )


def test_el_empty_state_ya_no_significa_no_elegible():
    """§8: ese mensaje deja de ser el resultado terminal de la acción."""
    assert "no está marcada como cliente" not in _JS


def test_k_el_click_en_la_oportunidad_sigue_siendo_independiente():
    """El camino Oportunidad → Detalle Oportunidades no se tocó."""
    codigo = _sin_comentarios(_JS)
    assert "detalleFilterOpportunityId = Number(licenciasOpportunityRow.dataset" in codigo
    assert 'commercialSetCapability("detalle")' in codigo
